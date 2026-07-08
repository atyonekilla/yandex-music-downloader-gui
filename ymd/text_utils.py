def remove_characters_to_satisfy_byte_length(
    text: str, encoding: str, target_length: int
) -> str:
    total_byte_length = len(text.encode(encoding))
    last_index = len(text)
    while total_byte_length > target_length:
        last_index -= 1
        total_byte_length -= len(text[last_index].encode(encoding))
    return text[:last_index]
