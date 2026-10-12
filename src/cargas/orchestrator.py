"""Orquestador central de la conversación con el camionero.

Un único flujo, sin agentes autónomos:

    mensaje → Parser → (faltan datos esenciales? preguntar sólo eso)
            → Matching (elegibilidad + ranking) → Respuesta con las mejores opciones
            → elección → Agente comercial (simulado) → aprobación explícita → Reserva

El estado del diálogo vive en `conversations.state` (JSON), así que cada mensaje se procesa en
una transacción independiente: no hay procesos permanentes por agente ni por empresa.
"""

import re
import uuid
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cargas.channels.base import InboundMessage, OutboundMessage
from cargas.commercial.agent import (
    NegotiationPolicy,
    QuoteRequest,
    ShipperChannel,
    SimulatedShipperChannel,
    negotiate,
)
from cargas.contacts import contacts_for_trip
from cargas.geo.places import normalize, resolve_place
from cargas.geo.routing import Router
from cargas.matching.engine import AgentPolicy, TripSpec, explain, rank
from cargas.models import (
    Booking,
    Conversation,
    Load,
    LoadSource,
    Match,
    Message,
    Negotiation,
    Trip,
    Truck,
    User,
)
from cargas.parser.schemas import FIELD_QUESTIONS, TripDraft
from cargas.parser.service import MessageParser
from cargas.services import (
    agent_policy,
    audit,
    candidate_loads,
    get_agent,
    load_snapshot,
    load_spec,
    trip_spec,
)

OPTIONS_SHOWN = 3
EXAMPLE = "Por ejemplo: «Vuelvo mañana de Córdoba a Buenos Aires vacío»."
# Motivo de descarte → (singular, plural).
REJECTION_LABELS = {
    "ruta": ("fuera de ruta", "fuera de ruta"),
    "fecha": ("con fecha incompatible", "con fecha incompatible"),
    "vencida": ("vencida", "vencidas"),
    "no_disponible": ("ya tomada", "ya tomadas"),
    "capacidad": ("sin capacidad suficiente", "sin capacidad suficiente"),
    "vehiculo": ("para otro tipo de vehículo", "para otro tipo de vehículo"),
    "carroceria": ("para otra carrocería", "para otra carrocería"),
    "refrigeracion": ("que requiere frío", "que requieren frío"),
    "mercaderia": ("con mercadería excluida", "con mercadería excluida"),
    "precio": ("con precio debajo de tu mínimo", "con precio debajo de tu mínimo"),
}
_YES = re.compile(r"^(si|sí|dale|confirmo|confirmar|ok|de una|acepto)\b")
_NO = re.compile(r"^(no|cancelar|cancelo|rechazo|mejor no)\b")
_DISCARD = re.compile(r"\b(descart\w*|no me sirve|saca\w*)\b\D*(\d)")
_SELECT = re.compile(
    r"^(?:quiero |elijo |tomo |acepto |me interesa |dale |opcion |la |el )*"
    r"(?:opcion |la |el |numero )?(\d)\b"
)
_RESET = re.compile(r"^(nuevo|nueva busqueda|empezar de nuevo|reiniciar|cancelar busqueda)\b")


def _ars(value: Decimal | float | str) -> str:
    return f"${Decimal(str(value)):,.0f}".replace(",", ".")


class Orchestrator:
    def __init__(
        self, parser: MessageParser, router: Router, shipper_channel: ShipperChannel | None = None
    ) -> None:
        self.parser = parser
        self.router = router
        self.shipper_channel = shipper_channel or SimulatedShipperChannel()

    # ------------------------------------------------------------------ entrada principal

    def handle(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        inbound: InboundMessage,
        now: datetime,
        today: date,
    ) -> list[OutboundMessage]:
        session.add(
            Message(
                company_id=user.company_id,
                conversation_id=conversation.id,
                direction="in",
                text=inbound.text,
            )
        )
        state = dict(conversation.state or {})
        text = normalize(inbound.text)

        if _RESET.match(text):
            state = {}
            replies = [
                OutboundMessage("Listo, empezamos de nuevo. Contame tu viaje de vuelta. " + EXAMPLE)
            ]
        else:
            stage = state.get("stage", "idle")
            if stage == "confirming":
                replies = self._on_confirming(session, user, state, text, now)
            elif stage == "choosing":
                replies = self._on_choosing(
                    session, user, conversation, state, inbound.text, text, now, today
                )
            else:
                replies = self._on_trip_message(
                    session, user, conversation, state, inbound.text, now, today
                )

        conversation.state = state
        for reply in replies:
            session.add(
                Message(
                    company_id=user.company_id,
                    conversation_id=conversation.id,
                    direction="out",
                    text=reply.text,
                    payload=reply.payload,
                )
            )
        return replies

    # --------------------------------------------------------------- 1. datos del viaje

    def _on_trip_message(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        state: dict[str, Any],
        raw_text: str,
        now: datetime,
        today: date,
    ) -> list[OutboundMessage]:
        result = self.parser.parse(raw_text, today)
        new = result.draft
        previous = TripDraft.model_validate(state["draft"]) if state.get("draft") else TripDraft()
        draft = previous.merged_with(new)
        state["parse_method"] = result.method
        state["raw_text"] = " / ".join(filter(None, [state.get("raw_text"), raw_text]))

        notes: list[str] = []
        if draft.departure_date and draft.departure_date < today:
            notes.append("La fecha que indicaste ya pasó.")
            draft.departure_date = None
        if draft.origin and draft.origin == draft.destination:
            notes.append("El origen y el destino son la misma ciudad.")
            draft.destination = None

        trucks = session.scalars(
            select(Truck)
            .where(Truck.company_id == user.company_id, Truck.active.is_(True))
            .order_by(Truck.plate)
        ).all()
        if not trucks:
            state.update(stage="collecting", draft=draft.model_dump(mode="json"))
            return [
                OutboundMessage(
                    "Todavía no tenés camiones registrados. Cargá uno en "
                    "Configuración y volvé a escribirme tu viaje."
                )
            ]
        truck, truck_question = self._pick_truck(list(trucks), draft)

        nothing_understood = new == TripDraft() and not state.get("draft")
        if nothing_understood:
            state.clear()
            return [
                OutboundMessage(
                    "No pude identificar un viaje en tu mensaje. Decime desde "
                    "dónde salís, a dónde volvés y qué día. " + EXAMPLE,
                    {"kind": "parse_error"},
                )
            ]

        missing = draft.missing_essentials()
        questions = [FIELD_QUESTIONS[f] for f in missing]
        if truck is None:
            questions.append(truck_question)
        if questions:
            state.update(stage="collecting", draft=draft.model_dump(mode="json"))
            understood = self._summarize_draft(draft)
            text = " ".join([*notes, f"Entendí: {understood}." if understood else "", *questions])
            return [OutboundMessage(text.strip(), {"kind": "question", "missing": missing})]

        assert truck is not None
        return self._search(session, user, conversation, state, draft, truck, now)

    @staticmethod
    def _pick_truck(trucks: list[Truck], draft: TripDraft) -> tuple[Truck | None, str]:
        if draft.plate:
            by_plate = [t for t in trucks if t.plate.replace(" ", "").upper() == draft.plate]
            if by_plate:
                return by_plate[0], ""
        if len(trucks) == 1:
            return trucks[0], ""
        candidates = trucks
        if draft.vehicle_type:
            candidates = [t for t in candidates if t.vehicle_type == draft.vehicle_type]
        if draft.refrigerated:
            candidates = [t for t in candidates if t.refrigerated]
        if len(candidates) == 1:
            return candidates[0], ""
        plates = ", ".join(
            f"{t.plate} ({t.vehicle_type}, {t.capacity_pallets} pallets)"
            for t in (candidates or trucks)
        )
        return None, f"¿Con qué camión viajás? {plates}."

    @staticmethod
    def _summarize_draft(draft: TripDraft) -> str:
        parts = []
        if draft.origin or draft.destination:
            parts.append(f"{draft.origin or '?'} → {draft.destination or '?'}")
        if draft.departure_date:
            parts.append(f"salida {draft.departure_date:%d/%m}")
        if draft.vehicle_type:
            parts.append(draft.vehicle_type)
        if draft.pallets:
            parts.append(f"{draft.pallets} pallets")
        return ", ".join(parts)

    # --------------------------------------------------------------- 2. búsqueda y ranking

    def _search(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        state: dict[str, Any],
        draft: TripDraft,
        truck: Truck,
        now: datetime,
    ) -> list[OutboundMessage]:
        origin, dest = resolve_place(draft.origin or ""), resolve_place(draft.destination or "")
        assert origin and dest and draft.departure_date
        trip = Trip(
            company_id=user.company_id,
            conversation_id=conversation.id,
            truck_id=truck.id,
            requested_by=user.id,
            origin_city=origin.name,
            origin_lat=origin.lat,
            origin_lon=origin.lon,
            dest_city=dest.name,
            dest_lat=dest.lat,
            dest_lon=dest.lon,
            departure_date=draft.departure_date,
            available_pallets=min(draft.pallets or truck.capacity_pallets, truck.capacity_pallets),
            available_kg=min(draft.weight_kg or truck.capacity_kg, truck.capacity_kg),
            raw_text=state.get("raw_text"),
            parse_method=state.get("parse_method", "rules"),
        )
        session.add(trip)
        session.flush()
        audit(
            session,
            user.company_id,
            user.id,
            "trip_created",
            "trip",
            trip.id,
            origin=trip.origin_city,
            destination=trip.dest_city,
            departure_date=trip.departure_date.isoformat(),
            truck=truck.plate,
            parse_method=trip.parse_method,
        )

        agent = get_agent(session, user.company_id)
        policy = agent_policy(agent)
        spec = trip_spec(trip, truck)
        candidates = candidate_loads(session, trip)
        sources = {load.id: (load, source) for load, source in candidates}
        eligible, rejected = rank(
            spec, [load_spec(lo, so) for lo, so in candidates], policy, self.router, now
        )

        option_ids: list[str] = []
        for position, ev in enumerate(eligible, start=1):
            load, source = sources[ev.load.id]
            match = Match(
                company_id=user.company_id,
                trip_id=trip.id,
                load_id=load.id,
                rank=position,
                score=ev.score,
                components=ev.components,
                metrics={
                    "base_km": ev.route.base_km,
                    "detour_km": ev.route.detour_km,
                    "loaded_km": ev.route.loaded_km,
                    "total_km": round(ev.route.total_km, 1),
                    "distance_method": ev.route.distance_method,
                    "net_gain_ars": str(ev.net_gain.quantize(Decimal(1))),
                    "floor_price_ars": str(ev.floor_price),
                    "pickup_date": ev.pickup_date.isoformat() if ev.pickup_date else None,
                    "flags": ev.flags,
                },
                explanation=explain(ev, spec),
                load_snapshot=load_snapshot(load, source, now),
            )
            session.add(match)
            session.flush()
            option_ids.append(str(match.id))

        reasons = Counter(ev.rejections[0].code for ev in rejected)
        audit(
            session,
            user.company_id,
            user.id,
            "search_completed",
            "trip",
            trip.id,
            evaluated=len(candidates),
            eligible=len(eligible),
            rejected=dict(reasons),
        )
        header = (
            f"Tu vuelta {trip.origin_city} → {trip.dest_city}, salida "
            f"{trip.departure_date:%d/%m}, {truck.vehicle_type} {truck.plate} con "
            f"{trip.available_pallets} pallets libres. Revisé {len(candidates)} ofertas."
        )
        discarded = self._rejection_summary(reasons)

        contacts = self._contacts_message(session, user, trip, spec, policy)
        if not eligible:
            trip.status = "no_results"
            state.clear()
            replies = [
                OutboundMessage(
                    f"{header} No encontré cargas compatibles. {discarded} Podés ampliar el desvío "
                    "máximo o los días de flexibilidad en Configuración, o probar con otra fecha.",
                    {"kind": "no_results", "trip_id": str(trip.id), "rejected": dict(reasons)},
                )
            ]
            return replies + ([contacts] if contacts else [])

        trip.status = "options_sent"
        state.clear()
        state.update(stage="choosing", trip_id=str(trip.id), options=option_ids, offset=0)
        options = self._options_message(
            session, state, header=f"{header} Encontré {len(eligible)} compatibles. {discarded}"
        )
        return [options] + ([contacts] if contacts else [])

    def _contacts_message(
        self, session: Session, user: User, trip: Trip, spec: TripSpec, policy: AgentPolicy
    ) -> OutboundMessage | None:
        """Dadores de la agenda de la empresa que cargan en el origen o sobre la ruta."""
        suggestions = contacts_for_trip(
            session,
            user.company_id,
            spec,
            self.router,
            policy.max_detour_km,
            policy.excluded_cargo_types,
        )
        if not suggestions:
            return None
        audit(
            session,
            user.company_id,
            user.id,
            "contacts_suggested",
            "trip",
            trip.id,
            contacts=[str(sg.contact.id) for sg in suggestions],
        )
        lines = ["De tu agenda, estos dadores cargan sobre tu ruta y podrías consultarlos:"]
        cards = []
        for sg in suggestions:
            where = sg.city if sg.detour_km < 15 else f"{sg.city}, desvío {sg.detour_km:.0f} km"
            reach = " · ".join(
                filter(
                    None,
                    [
                        f"tel. {sg.contact.phone}" if sg.contact.phone else None,
                        sg.contact.email,
                    ],
                )
            )
            lines.append(f"• {sg.contact.name} ({where}) — {reach}")
            cards.append(
                {
                    "id": str(sg.contact.id),
                    "name": sg.contact.name,
                    "city": sg.city,
                    "detour_km": sg.detour_km,
                    "phone": sg.contact.phone,
                    "email": sg.contact.email,
                }
            )
        lines.append(
            "(Por ahora el agente no les escribe: la consulta automática llega con la "
            "integración de WhatsApp.)"
        )
        return OutboundMessage("\n".join(lines), {"kind": "contacts", "contacts": cards})

    @staticmethod
    def _rejection_summary(reasons: Counter[str]) -> str:
        if not reasons:
            return ""
        parts = [
            f"{n} {REJECTION_LABELS.get(code, (code, code))[0 if n == 1 else 1]}"
            for code, n in reasons.most_common()
        ]
        return "Descarté: " + ", ".join(parts) + "."

    def _options_message(
        self, session: Session, state: dict[str, Any], header: str = ""
    ) -> OutboundMessage:
        active = self._active_options(session, state)
        if not active:
            state.clear()
            return OutboundMessage(
                f"{header} No quedan más opciones para este viaje. "
                "Escribime otro viaje cuando quieras.".strip(),
                {"kind": "no_results"},
            )
        shown = active[:OPTIONS_SHOWN]
        lines = [header.strip()] if header else []
        cards = []
        for number, match in enumerate(shown, start=1):
            snap = match.load_snapshot
            lines.append(
                f"{number}) {snap['origin']} → {snap['destination']} · {snap['description']} · "
                f"{_ars(snap['price_ars'])} · puntaje {match.score:.0f}/100\n"
                f"   {match.explanation}"
            )
            cards.append(
                {
                    "number": number,
                    "match_id": str(match.id),
                    "score": match.score,
                    "components": match.components,
                    "metrics": match.metrics,
                    "load": snap,
                    "explanation": match.explanation,
                }
            )
        state["shown"] = [str(m.id) for m in shown]
        lines.append(
            "Respondé con el número de la opción para que contacte al dador, "
            "o «descartar N» si no te sirve."
        )
        return OutboundMessage(
            "\n".join(lines), {"kind": "options", "options": cards, "trip_id": state.get("trip_id")}
        )

    @staticmethod
    def _active_options(session: Session, state: dict[str, Any]) -> list[Match]:
        ids = [uuid.UUID(i) for i in state.get("options", [])]
        if not ids:
            return []
        matches = session.scalars(select(Match).where(Match.id.in_(ids))).all()
        return sorted((m for m in matches if m.status == "proposed"), key=lambda m: m.rank)

    # --------------------------------------------------------------- 3. elección

    def _on_choosing(
        self,
        session: Session,
        user: User,
        conversation: Conversation,
        state: dict[str, Any],
        raw_text: str,
        text: str,
        now: datetime,
        today: date,
    ) -> list[OutboundMessage]:
        shown = state.get("shown", [])
        discard = _DISCARD.search(text)
        if discard:
            match = self._shown_match(session, shown, int(discard.group(2)))
            if match is None:
                return [OutboundMessage(f"No hay una opción {discard.group(2)}.")]
            match.status = "discarded"
            audit(
                session,
                user.company_id,
                user.id,
                "match_discarded",
                "match",
                match.id,
                load_id=str(match.load_id),
            )
            session.flush()
            return [self._options_message(session, state, header="Descartada.")]

        select_m = _SELECT.match(text)
        if select_m:
            match = self._shown_match(session, shown, int(select_m.group(1)))
            if match is None:
                return [OutboundMessage(f"No hay una opción {select_m.group(1)}.")]
            return self._contact_shipper(session, user, state, match, now)

        # ¿Es un viaje nuevo? Se descarta la lista actual y se empieza otra búsqueda.
        draft = self.parser.parse(raw_text, today).draft
        if draft.origin and draft.destination:
            state.clear()
            return self._on_trip_message(session, user, conversation, state, raw_text, now, today)
        return [
            OutboundMessage(
                "Respondé con el número de la opción (1, 2 o 3), «descartar N» "
                "o «nuevo» para buscar otro viaje."
            )
        ]

    @staticmethod
    def _shown_match(session: Session, shown: list[str], number: int) -> Match | None:
        if not 1 <= number <= len(shown):
            return None
        match = session.get(Match, uuid.UUID(shown[number - 1]))
        return match if match and match.status == "proposed" else None

    # --------------------------------------------------------------- 4. contacto (simulado)

    def _contact_shipper(
        self, session: Session, user: User, state: dict[str, Any], match: Match, now: datetime
    ) -> list[OutboundMessage]:
        load = session.get(Load, match.load_id)
        assert load is not None
        source = session.get(LoadSource, load.primary_source_id)
        agent = get_agent(session, user.company_id)
        match.status = "selected"
        audit(
            session,
            user.company_id,
            user.id,
            "match_selected",
            "match",
            match.id,
            load_id=str(load.id),
            score=match.score,
        )

        floor = Decimal(match.metrics["floor_price_ars"])
        outcome = negotiate(
            QuoteRequest(
                load_ref=str(load.id),
                description=load.description,
                listed_price=load.price_ars,
                ask_price=load.price_ars,
                round=0,
                simulation=(load.raw or {}).get("simulation", {}),
            ),
            NegotiationPolicy(
                floor_price=floor,
                max_rounds=agent.max_negotiation_rounds,
                max_counter_uplift_pct=agent.max_counter_uplift_pct,
            ),
            self.shipper_channel,
            now,
        )
        negotiation = Negotiation(
            company_id=user.company_id,
            match_id=match.id,
            channel=self.shipper_channel.name,
            status=outcome.status,
            floor_price=floor,
            listed_price=load.price_ars,
            agreed_price=outcome.agreed_price,
            transcript=outcome.transcript,
        )
        session.add(negotiation)
        session.flush()
        audit(
            session,
            user.company_id,
            user.id,
            "negotiation_finished",
            "negotiation",
            negotiation.id,
            status=outcome.status,
            channel=self.shipper_channel.name,
            agreed_price=str(outcome.agreed_price) if outcome.agreed_price else None,
            source_is_demo=bool(source and source.is_demo),
        )
        simulated = (
            " (Contacto SIMULADO: no se envió ningún mensaje real.)"
            if self.shipper_channel.name == "simulated"
            else ""
        )
        payload = {
            "kind": "negotiation",
            "negotiation_id": str(negotiation.id),
            "status": outcome.status,
            "transcript": outcome.transcript,
        }

        if outcome.status == "awaiting_driver":
            assert outcome.agreed_price is not None
            state.update(stage="confirming", negotiation_id=str(negotiation.id))
            return [
                OutboundMessage(
                    f"Contacté a {load.shipper_name}.{simulated} {outcome.summary} "
                    f"{load.origin_city} → {load.dest_city}, {load.description}, "
                    f"precio {_ars(outcome.agreed_price)}. ¿Confirmás la operación? (sí / no)",
                    payload,
                )
            ]

        match.status = "unavailable" if outcome.status == "unavailable" else "rejected"
        session.flush()
        follow = self._options_message(session, state, header="Te muestro las que quedan:")
        return [
            OutboundMessage(
                f"Contacté a {load.shipper_name}.{simulated} {outcome.summary}", payload
            ),
            follow,
        ]

    # --------------------------------------------------------------- 5. aprobación

    def _on_confirming(
        self, session: Session, user: User, state: dict[str, Any], text: str, now: datetime
    ) -> list[OutboundMessage]:
        negotiation = session.get(Negotiation, uuid.UUID(state["negotiation_id"]))
        assert negotiation is not None
        match = session.get(Match, negotiation.match_id)
        assert match is not None
        trip = session.get(Trip, match.trip_id)
        assert trip is not None

        if _YES.match(text):
            assert negotiation.agreed_price is not None
            booking = Booking(
                company_id=user.company_id,
                trip_id=trip.id,
                match_id=match.id,
                negotiation_id=negotiation.id,
                load_id=match.load_id,
                agreed_price=negotiation.agreed_price,
                approved_by=user.id,
                approved_at=now,
            )
            session.add(booking)
            negotiation.status = "confirmed"
            match.status = "booked"
            trip.status = "booked"
            session.flush()
            audit(
                session,
                user.company_id,
                user.id,
                "booking_confirmed",
                "booking",
                booking.id,
                trip_id=str(trip.id),
                load_id=str(match.load_id),
                agreed_price=str(booking.agreed_price),
                status=booking.status,
            )
            snap = match.load_snapshot
            state.clear()
            return [
                OutboundMessage(
                    f"Operación registrada: {snap['origin']} → {snap['destination']}, "
                    f"{snap['description']}, {_ars(booking.agreed_price)}. Dador: "
                    f"{snap['shipper_name']}. Estado: confirmada en simulación (en el MVP no se "
                    "envía la confirmación real al dador).",
                    {"kind": "booking", "booking_id": str(booking.id)},
                )
            ]
        if _NO.match(text):
            negotiation.status = "declined_by_driver"
            match.status = "discarded"
            session.flush()
            audit(
                session, user.company_id, user.id, "booking_declined", "negotiation", negotiation.id
            )
            state["stage"] = "choosing"
            state.pop("negotiation_id", None)
            return [
                self._options_message(
                    session, state, header="No se confirma. Te muestro las otras opciones:"
                )
            ]
        return [
            OutboundMessage("Respondé «sí» para confirmar la operación o «no» para descartarla.")
        ]
