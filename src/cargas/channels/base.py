"""Mensajes independientes del canal.

El orquestador recibe `InboundMessage` y devuelve `OutboundMessage`; no sabe si la conversación
ocurre en el chat web, WhatsApp o una prueba. Un adaptador de canal sólo traduce:

- Chat web (MVP): POST /api/chat/messages → InboundMessage(channel="web").
- WhatsApp (futuro): webhook del proveedor → identificar al usuario por `users.phone` →
  InboundMessage(channel="whatsapp"); cada OutboundMessage se envía con la API del proveedor
  (los `payload.options` se pueden mapear a mensajes interactivos con botones).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InboundMessage:
    channel: str
    text: str


@dataclass
class OutboundMessage:
    text: str
    payload: dict[str, Any] = field(default_factory=dict)
