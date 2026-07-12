from slowapi import Limiter
from slowapi.util import get_remote_address


# headers_enabled agrega X-RateLimit-* y Retry-After a las respuestas 429,
# para que los clientes sepan cuando reintentar.
limiter = Limiter(key_func=get_remote_address, headers_enabled=True)
