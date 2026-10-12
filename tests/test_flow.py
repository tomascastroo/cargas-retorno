"""Escenario completo del MVP a través de la API HTTP (punto 8 del pedido)."""

from conftest import Chat, auth, signup
from fastapi.testclient import TestClient


def options(replies: list[dict]) -> list[dict]:
    for reply in replies:
        if reply["payload"].get("kind") == "options":
            return reply["payload"]["options"]
    raise AssertionError(f"sin opciones en {replies}")


def test_full_scenario(client: TestClient) -> None:
    # 1. Crear una empresa de transporte.
    company = signup(client, "Transportes Escenario")
    headers = auth(company)
    # 2. Registrar un semi de 28 pallets.
    resp = client.post(
        "/api/trucks",
        headers=headers,
        json={
            "plate": "AE 100 ZZ",
            "vehicle_type": "semi",
            "body_type": "sider",
            "capacity_pallets": 28,
            "capacity_kg": 30000,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["plate"] == "AE100ZZ"
    # La empresa no transporta mercadería peligrosa.
    agent = client.get("/api/agent", headers=headers).json()
    agent["excluded_cargo_types"] = ["peligrosa"]
    assert client.put("/api/agent", headers=headers, json=agent).status_code == 200
    # 3. Las oportunidades demo ya están cargadas (conftest → ingesta).
    assert len(client.get("/api/loads", headers=headers).json()) > 10

    # 4-5. Mensaje del camionero e interpretación.
    chat = Chat(client, company)
    replies = chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío")
    trip = client.get("/api/trips", headers=headers).json()[0]
    assert (trip["origin_city"], trip["dest_city"]) == ("Córdoba", "Buenos Aires")
    assert trip["available_pallets"] == 28
    assert trip["parse_method"] == "rules"

    # 6-7. Cargas compatibles ordenadas, con explicación.
    opts = options(replies)
    assert len(opts) == 3
    assert [o["score"] for o in opts] == sorted((o["score"] for o in opts), reverse=True)
    assert opts[0]["load"]["description"] == "Harina en bolsas, paletizada"
    assert "recorrido" in opts[0]["explanation"]
    assert set(opts[0]["components"]) == {
        "rentabilidad",
        "desvio",
        "fecha",
        "ocupacion",
        "confiabilidad",
    }
    text = replies[0]["text"]
    assert "Descarté:" in text and "fuera de ruta" in text and "vencida" in text
    detail = client.get(f"/api/trips/{trip['id']}", headers=headers).json()
    shown = {m["load_snapshot"]["description"] for m in detail["matches"]}
    # Las incompatibles nunca se proponen.
    for excluded in (
        "Carne vacuna envasada al vacío",
        "Cajas de cartón plegadas",
        "Alimento balanceado (oferta vencida)",
        "Envases de vidrio",
        "Agroquímicos (clase 6.1)",
        "Muebles en kit (ya tomada)",
        "Semillas en bolsa",
        "Bobinas de chapa",
        "Electrodomésticos",
    ):
        assert excluded not in shown
    # La oferta duplicada (JSON + CSV) aparece una sola vez.
    assert sum(1 for m in detail["matches"] if "Harina" in m["load_snapshot"]["description"]) == 1

    # 8. Elegir la primera opción → contacto simulado con el dador.
    replies = chat.say("1")
    negotiation = replies[0]["payload"]
    assert negotiation["kind"] == "negotiation" and negotiation["status"] == "awaiting_driver"
    assert "SIMULADO" in replies[0]["text"]
    # Sin aprobación explícita no hay reserva.
    assert client.get("/api/bookings", headers=headers).json() == []

    # 9. Aprobar y registrar el resultado.
    replies = chat.say("Sí, confirmo")
    assert replies[0]["payload"]["kind"] == "booking"
    bookings = client.get("/api/bookings", headers=headers).json()
    assert len(bookings) == 1
    assert bookings[0]["status"] == "simulated_confirmed"
    assert float(bookings[0]["agreed_price"]) == 1_950_000
    assert client.get(f"/api/trips/{trip['id']}", headers=headers).json()["status"] == "booked"
    actions = [e["action"] for e in client.get("/api/audit-events", headers=headers).json()]
    for action in (
        "company_created",
        "truck_created",
        "agent_updated",
        "trip_created",
        "search_completed",
        "match_selected",
        "negotiation_finished",
        "booking_confirmed",
    ):
        assert action in actions
    messages = client.get(
        f"/api/conversations/{chat.conversation_id}/messages", headers=headers
    ).json()
    assert [m["direction"] for m in messages] == ["in", "out"] * 3

    # 10. Otra empresa no puede ver nada de lo anterior.
    other = auth(signup(client, "Competencia SA"))
    assert client.get("/api/trips", headers=other).json() == []
    assert client.get("/api/bookings", headers=other).json() == []
    assert client.get("/api/trucks", headers=other).json() == []
    for path in (
        f"/api/trips/{trip['id']}",
        f"/api/bookings/{bookings[0]['id']}",
        f"/api/negotiations/{negotiation['negotiation_id']}",
        f"/api/conversations/{chat.conversation_id}/messages",
    ):
        assert client.get(path, headers=other).status_code == 404, path
    hijack = client.post(
        "/api/chat/messages",
        headers=other,
        json={"text": "1", "conversation_id": chat.conversation_id},
    )
    assert hijack.status_code == 404
    audit_other = client.get("/api/audit-events", headers=other).json()
    assert {e["action"] for e in audit_other} == {"company_created"}
    # Las oportunidades públicas sí son compartidas.
    assert len(client.get("/api/loads", headers=other).json()) > 10


def test_asks_only_for_missing_data(chat: Chat) -> None:
    replies = chat.say("Vuelvo a Buenos Aires")
    assert replies[0]["payload"]["missing"] == ["origin", "departure_date"]
    assert "¿Desde qué ciudad" in replies[0]["text"]
    assert "¿A qué ciudad" not in replies[0]["text"]
    replies = chat.say("de Córdoba")
    assert replies[0]["payload"]["missing"] == ["departure_date"]
    replies = chat.say("mañana")
    assert options(replies)


def test_parse_error_gives_example(chat: Chat) -> None:
    replies = chat.say("hola, ¿todo bien?")
    assert replies[0]["payload"]["kind"] == "parse_error"
    assert "Por ejemplo" in replies[0]["text"]


def test_no_results_explains_why(chat: Chat) -> None:
    replies = chat.say("Vuelvo mañana de Comodoro Rivadavia a Trelew")
    assert replies[0]["payload"]["kind"] == "no_results"
    assert "No encontré cargas compatibles" in replies[0]["text"]


def test_unavailable_shipper_then_show_remaining_options(client: TestClient, chat: Chat) -> None:
    opts = options(chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío"))
    # Descarta las dos primeras hasta que aparezca la de San Francisco (dador no disponible).
    target = next((o for o in opts if "maquinaria" in o["load"]["description"]), None)
    while target is None:
        opts = options(chat.say("descartar 1"))
        target = next((o for o in opts if "maquinaria" in o["load"]["description"]), None)
    replies = chat.say(str(target["number"]))
    assert replies[0]["payload"]["status"] == "unavailable"
    remaining = options(replies[1:])
    assert all("maquinaria" not in o["load"]["description"] for o in remaining)


def test_counter_offer_and_driver_declines(client: TestClient, chat: Chat) -> None:
    opts = options(chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío"))
    leche = next(o for o in opts if "Leche" in o["load"]["description"])
    replies = chat.say(f"quiero la {leche['number']}")
    assert replies[0]["payload"]["status"] == "awaiting_driver"
    replies = chat.say("no")
    assert "otras opciones" in replies[0]["text"]
    assert client.get("/api/bookings", headers=chat.headers).json() == []


def test_new_trip_while_choosing_starts_new_search(client: TestClient, chat: Chat) -> None:
    chat.say("Vuelvo mañana de Córdoba a Buenos Aires vacío")
    replies = chat.say("Mejor vuelvo mañana de Mendoza a Buenos Aires")
    assert options(replies)[0]["load"]["origin"] == "Mendoza"
    assert len(client.get("/api/trips", headers=chat.headers).json()) == 2


def test_company_without_trucks_is_asked_to_register_one(client: TestClient) -> None:
    replies = Chat(client, signup(client)).say("Vuelvo mañana de Córdoba a Buenos Aires")
    assert "no tenés camiones" in replies[0]["text"]


def test_auth_required_and_bad_credentials(client: TestClient) -> None:
    assert client.get("/api/trips").status_code == 401
    assert client.get("/api/trips", headers={"authorization": "Bearer x.y"}).status_code == 401
    bad = client.post("/api/auth/login", json={"email": "nadie@example.com", "password": "x"})
    assert bad.status_code == 401


def test_login_and_duplicate_email(client: TestClient) -> None:
    body = {
        "company_name": "Login SA",
        "admin_name": "Ana",
        "admin_email": "login@example.com",
        "password": "secreto-123",
    }
    assert client.post("/api/companies", json=body).status_code == 201
    assert client.post("/api/companies", json=body).status_code == 409
    resp = client.post(
        "/api/auth/login", json={"email": "LOGIN@example.com", "password": "secreto-123"}
    )
    assert resp.status_code == 200
    me = client.get("/api/me", headers={"authorization": f"Bearer {resp.json()['token']}"})
    assert me.json()["company_name"] == "Login SA"


def test_agent_config_changes_matching(client: TestClient, company: dict[str, str]) -> None:
    headers = auth(company)
    agent = client.get("/api/agent", headers=headers).json()
    agent.update(max_detour_km=0, date_flex_days=0)
    assert client.put("/api/agent", headers=headers, json=agent).status_code == 200
    opts = options(Chat(client, company).say("Vuelvo mañana de Córdoba a Buenos Aires"))
    assert all(o["metrics"]["detour_km"] == 0 for o in opts)
