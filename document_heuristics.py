import re
from typing import Any

AI_OR_CONSUMER_TOOL = re.compile(
    r"chatgpt|openai|gpt-4|gpt-3|claude|anthropic|canva|gamma\.app|gamma app|notion\s*ai|"
    r"copilot|bard|gemini|writesonic|jasper|copy\.ai|quillbot|perplexity|llama|mistral|"
    r"adobe express|wkhtmltopdf|weasyprint|puppeteer|playwright|headless",
    re.I,
)
GOVT_OR_LEGAL_LIKE = re.compile(
    r"central bureau|cbi\b|ministry of|government of india|police department|official notice|"
    r"subpoena|arrest warrant|income tax|income-tax|directorate of enforcement|\bed\b|"
    r"enforcement directorate|cyber crime|cyber cell|federal bureau|irs\b|court order|"
    r"case id|file number|interpol|national investigation agency|\bnia\b|serious fraud|"
    r"economic offences|economic offense|fir\s*no|fir\s*number|\bfir\b|crime branch|"
    r"special cell|judicial magistrate|district court|high court|supreme court of india|"
    r"nodal officer|government of nct|\bmha\b|ministry of home|reserve bank|\brbi\b|"
    r"\bsebi\b|uidai|income tax officer|assistant commissioner|deputy commissioner|"
    r"customs and border",
    re.I,
)
PAYMENT_LIKE = re.compile(
    r"upi|imps|neft|rtgs|ifsc|swift|beneficiary|paytm|phonepe|google\s*pay|account\s*no|"
    r"a\/c\s*no|a\/c number|bank transfer|wire transfer|rupees|inr\b|₹|lakh|crore|"
    r"transfer the amount|deposit the|payable to|qr code|scan to pay|virtual account",
    re.I,
)
URGENCY_LIKE = re.compile(
    r"urgent|immediately|at once|without delay|within \d+\s*(hour|day|minute)|"
    r"failure to comply|will be arrested|arrest warrant|custody|Interpol|extradition|"
    r"account.?frozen|block.?all.?accounts|legal action will be taken|prosecution will|penalty of",
    re.I,
)
OFFICIALISH_FILENAME = re.compile(
    r"cbi|police|court|summons|notice|warrant|ed_|income|tax|fraud|case|interpol|rbi|"
    r"mha|ministry|govt|government|order|charge.?sheet|fir|cyber|enforcement",
    re.I,
)


def analyze_document_signals(
    text: str,
    filename: str = "",
    producer_metadata: str = "",
    page_count: int = 0,
    used_ocr: bool = False,
) -> dict[str, Any]:
    """Heuristics ported from cyber-fraud-app documentScan.js."""
    reasons: list[str] = []
    triggers: list[str] = []
    flags: set[str] = set()
    normalized = re.sub(r"\s+", " ", (text or "")).strip()
    producer = producer_metadata or ""

    if AI_OR_CONSUMER_TOOL.search(producer):
        reasons.append("PDF metadata references a consumer or AI authoring tool.")
        triggers.append("metadata-tool")
        flags.add("DOC_METADATA_ANOMALY")

    if GOVT_OR_LEGAL_LIKE.search(normalized) and AI_OR_CONSUMER_TOOL.search(producer):
        reasons.append("Official-sounding text with consumer/AI PDF metadata.")
        triggers.append("official-tone-plus-tool-metadata")
        flags.add("FAKE_OFFICIAL_DOC")

    if OFFICIALISH_FILENAME.search(filename) and len(normalized) < 100 and page_count >= 1:
        reasons.append("Official-looking filename but very little extractable text.")
        triggers.append("thin-text-official-name")
        flags.add("FAKE_OFFICIAL_DOC")

    if GOVT_OR_LEGAL_LIKE.search(normalized) and PAYMENT_LIKE.search(normalized):
        reasons.append("Official-sounding wording combined with payment instructions.")
        triggers.append("govt-plus-payment")
        flags.add("DOC_PAYMENT_PRESSURE")

    if GOVT_OR_LEGAL_LIKE.search(normalized) and URGENCY_LIKE.search(normalized):
        reasons.append("Official or legal tone combined with extreme urgency.")
        triggers.append("govt-plus-urgency")
        flags.add("FAKE_OFFICIAL_DOC")

    # Each document trigger adds risk; cap contribution.
    document_delta = min(len(set(triggers)) * 3, 9)

    return {
        "document_delta": document_delta,
        "document_flags": sorted(flags),
        "document_triggers": sorted(set(triggers)),
        "document_reasons": reasons,
        "used_ocr": used_ocr,
        "text_length": len(normalized),
    }
