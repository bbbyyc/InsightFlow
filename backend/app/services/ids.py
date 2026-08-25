from uuid import UUID


def parse_uuid(value) -> UUID | None:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def require_uuid(value) -> UUID:
    parsed = parse_uuid(value)
    if parsed is None:
        raise ValueError(f"Invalid UUID: {value}")
    return parsed
