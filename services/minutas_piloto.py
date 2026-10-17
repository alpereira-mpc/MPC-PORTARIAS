"""Acesso ao Minutas IA para identidades já autenticadas no portal."""

import re

from services.access import normalize_email


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def can_access_minutas_piloto(identity, secrets=None):
    """Qualquer identidade verificada já autorizada no portal.

    A lista MINUTAS_PILOTO_EMAILS e a flag MINUTAS_PILOTO_ENABLED não restringem
    mais o módulo. Acesso anônimo e e-mail não verificado continuam negados.
    O parâmetro secrets permanece na assinatura para os chamadores existentes.
    """
    del secrets
    if not identity or identity.get("email_verified") is False:
        return False
    try:
        email = normalize_email(identity.get("email"))
    except Exception:
        return False
    return bool(email and _EMAIL_PATTERN.fullmatch(email))
