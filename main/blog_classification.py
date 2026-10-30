"""Conservative, overlapping labels for sampled content, not verdicts about authors."""

import re
from collections import Counter

from main import support_impersonation

CATEGORIES = {
    "promotional": "Promotional spam",
    "gallery_promotion": "Automated gallery promotion",
    "support_impersonation": "Support impersonation",
    "pornography": "Pornography",
    "gambling": "Gambling promotion",
    "commercial": "Commercial content",
    "personal": "Personal writing",
    "technical": "Technical writing",
    "links": "Link collection",
    "images": "Image galleries",
    "unclassified": "Unclassified",
    "empty": "Empty",
}

CTA = re.compile(
    r"\b(read (?:more|the full|the complete)|full article|continue reading|"
    r"visit (?:our|the) (?:site|website)|buy now|shop now|order now|"
    r"book (?:now|a consultation)|get a (?:free )?quote|contact us|"
    r"view full gallery)\b",
    re.I,
)
# Explicit terms need a distribution context. A discussion of pornography, sexual
# health, art or relationships is not itself a pornography finding.
EXPLICIT = re.compile(
    r"\b(porn(?:o|ography)?|xxx|hardcore|sex tape|sex videos?|nude (?:pics|photos)|"
    r"naked (?:pics|photos)|onlyfans leaks?|nsfw gallery|"
    r"porno|sikis|sikiş|sex hikayeleri|ensest)\b|"
    r"色情|成人视频|成人影片|偷拍|厕拍|廁拍|无码|無碼|سكس|порно",
    re.I,
)
DISTRIBUTION = re.compile(
    r"\b(view full gallery|curated gallery|watch (?:free|now|online)|"
    r"full (?:video|collection)|leaked (?:photos|videos|collection)|"
    r"free porn|porn (?:pics|videos)|download (?:video|now))\b|"
    r"在线观看|免費觀看|高清播放",
    re.I,
)
DISCUSSION = re.compile(
    r"\b(sexual health|sex education|porn addiction|pornography addiction|"
    r"consent education|research study|clinical|sexual violence prevention)\b",
    re.I,
)
GAMBLING = re.compile(
    r"\b(casino|sports betting|bookmaker|slot machines?|казино|ставки|"
    r"apostas|cassino)\b|博彩|赌场|賭場|คาสิโน|เว็บสล็อต",
    re.I,
)
GAMBLING_OFFER = re.compile(
    r"\b(bonus|bonuses|promo ?code|sign up|real money|free spins|"
    r"бонус|бонусы|промокод|реальные деньги|gratuit)\b|โบนัส|เครดิตฟรี|賠率|赔率",
    re.I,
)
COMMERCIAL = re.compile(
    r"\b(our (?:services|products|company|customers)|buy now|shop now|"
    r"order now|get a (?:free )?quote|contact us|book a consultation|"
    r"installation services|removal services|discount code|exam dumps|"
    r"luxury (?:products|brand)|verification service)\b",
    re.I,
)
PERSONAL = re.compile(
    r"\b(i (?:remember|felt|visited|went|started|learned|learnt|played|think|"
    r"wrote|moved|spent)|my (?:family|friend|life|childhood|journey|experience))\b",
    re.I,
)
TECHNICAL = re.compile(
    r"\b(python|javascript|typescript|compiler|programming|postgresql|"
    r"database|linux|docker|algorithm|source code|api|debugging)\b",
    re.I,
)


def item_labels(title, text, linked, image_count):
    content = title + "\n" + text
    labels = set()
    if support_impersonation.match(title, text):
        labels.add("support_impersonation")
    if (
        EXPLICIT.search(content)
        and DISTRIBUTION.search(content)
        and not DISCUSSION.search(content)
    ):
        labels.add("pornography")
    if linked and GAMBLING.search(content) and GAMBLING_OFFER.search(content):
        labels.add("gambling")
    if linked and COMMERCIAL.search(content):
        labels.add("commercial")
    if len(set(m.group().lower() for m in TECHNICAL.finditer(content))) >= 2:
        labels.add("technical")
    if len(PERSONAL.findall(text)) >= 2 and len(text) >= 250:
        labels.add("personal")
    if image_count >= 2:
        labels.add("images")
    return labels


def classify(
    examples, *, total, profile_text, profile_labels, promotional, thin, linked
):
    counts = Counter(label for example in examples for label in example["categories"])
    counts.update(profile_labels)
    reasons = {}
    if promotional:
        reasons["promotional"] = (
            "Combined outbound promotion signals; see the evidence below."
        )
    for category in ("pornography", "gambling", "commercial", "technical", "images"):
        if counts[category]:
            reasons[category] = {
                "pornography": "Explicit sexual terms together with gallery or video distribution wording",
                "gambling": "Gambling terms together with offers and outbound links",
                "commercial": "Product or service promotion with outbound links",
                "technical": "Multiple computing or programming terms",
                "images": "Multiple images",
            }[category] + f" in {counts[category]} sampled item(s) or blog settings."
    if counts["support_impersonation"]:
        reasons["support_impersonation"] = (
            "Brand-specific support or withdrawal articles repeat the same phone "
            f"number at least five times in each of {counts['support_impersonation']} "
            "sampled items. Phone ownership has not been verified."
        )
    if counts["personal"] and not (
        reasons.keys()
        & {
            "pornography",
            "promotional",
            "gambling",
            "commercial",
            "support_impersonation",
        }
    ):
        reasons["personal"] = (
            f"First-person experiences in {counts['personal']} sampled item(s) or blog settings."
        )
    if not promotional and linked >= 3 and thin >= len(examples) * 0.75:
        reasons["links"] = (
            "Most sampled items are short and link to outside sites, without stronger promotional signals."
        )
    if not reasons and not total and not profile_text.strip():
        reasons["empty"] = "No posts, pages, uploaded images or custom profile content."
    if not reasons:
        reasons["unclassified"] = (
            "The sample does not meet a category rule. This does not establish that the blog is personal or safe."
        )
    return reasons
