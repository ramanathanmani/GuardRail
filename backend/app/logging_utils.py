def mask_phone(phone: str) -> str:
    return phone[:-4] + "••••" if len(phone) > 4 else "••••"
