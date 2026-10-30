"""Review signals for the observed brand-support phone-stuffing pattern.

This first rule covers English titles and formatted North American numbers.
It does not verify phone ownership or establish that a caller would be scammed.
"""

import re
import unicodedata
from collections import Counter

BRAND = re.compile(
    r"\b(uniswap|metamask|blockchain|paybis|safepal|trust\s+wallet|moonpay|"
    r"shakepay|uphold|coinbase|kraken|binance|exodus|ledger|trezor|phantom|"
    r"robinhood|crypto\.com|atomic\s+wallet|fidelity)\b",
    re.I,
)
SUPPORT = re.compile(
    r"\b(support|help(?:line)?|customer\s+(?:service|care)|contact|"
    r"troubleshoot\w*|problems?|issues?|declined|stuck|"
    r"not\s+(?:working|showing|sending|arrived)|recover\w*|withdraw\w*|cash\s+out|"
    r"login|log\s+in|reset|locked|phone\s+number)\b",
    re.I,
)
WARNING = re.compile(
    r"\b(scam\w*|fraud\w*|phishing|fake|impersonat\w*|beware|warning|"
    r"do\s+not\s+call|don['’]t\s+call)\b",
    re.I,
)
PHONE = re.compile(
    r"(?<!\w)(?:\+?1[\s().-]{0,4})?"
    r"\(?([2-9][0-9]{2})\)?[\s.-]{0,3}"
    r"([2-9][0-9]{2})[\s.-]{0,3}([0-9]{4})(?!\w)"
)


def normalize(text):
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(
        "-" if unicodedata.category(char) == "Pd" else char
        for char in text
        if unicodedata.category(char) != "Cf"
    )


def match(title, text):
    title = normalize(title)
    brands = sorted(
        {" ".join(m.group().lower().split()) for m in BRAND.finditer(title)}
    )
    # Brand mentions, genuine contact details and warning articles are insufficient.
    # Safety boilerplate in the body is common in the spam itself, so only exempt
    # warnings in the title; the result remains a review signal, never a verdict.
    if not brands or not SUPPORT.search(title) or WARNING.search(title):
        return None
    text = normalize(text)
    if not SUPPORT.search(text):
        return None
    phones = Counter()
    for found in PHONE.finditer(text):
        if not re.search(r"[\s().-]", found.group()):
            continue  # Bare numeric IDs are not evidence of a helpline.
        phones["+1" + "".join(found.groups())] += 1
    repeated = [
        {"number": number, "count": count}
        for number, count in phones.most_common()
        if count >= 5
    ]
    return {"brands": brands, "phones": repeated} if repeated else None
