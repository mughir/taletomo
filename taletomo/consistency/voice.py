import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from taletomo.canon.models import Character, find_project_character
from taletomo.consistency.models import (
    ContinuityFinding,
    FindingCategory,
    FindingSeverity,
)

# Common speech attribution verbs
SPEECH_VERBS = (
    r"said|asked|replied|whispered|shouted|muttered|growled|snapped|murmured|"
    r"demanded|exclaimed|called|hissed|laughed|sighed|yelled|barked|grunted|"
    r"stated|insisted|pleaded|commanded|inquired|chuckled|roared"
)

# Contractions and informal slang that clash with formal/archaic styles
INFORMAL_CONTRACTIONS = {
    "gonna": "going to",
    "wanna": "want to",
    "gotta": "have to",
    "dunno": "do not know",
    "ain't": "is not / am not",
    "kinda": "kind of",
    "sorta": "sort of",
    "gimme": "give me",
    "lemme": "let me",
    "c'mon": "come on",
    "yeah": "yes",
    "nope": "no",
    "yep": "yes",
    "nah": "no",
    "dude": "sir / fellow",
    "bro": "brother / friend",
    "ok": "very well",
    "okay": "very well",
    "freaking": "confounded",
    "whatever": "as you wish",
}

# Modern anachronisms for pre-modern or high-fantasy settings
ANACHRONISMS = {
    "phone": "communication crystal / courier",
    "cellphone": "messenger",
    "computer": "archive / ledger",
    "internet": "network / web",
    "car": "carriage / mount",
    "airplane": "flying beast / airship",
    "okay": "agreed / very well",
    "ok": "agreed / very well",
    "cool": "impressive / pleasant",
    "super": "exceedingly / truly",
    "plastic": "parchment / horn",
}

FORMAL_INDICATORS = {
    "formal", "archaic", "refined", "aristocratic", "courtly", "noble",
    "solemn", "no contractions", "never uses contractions", "dignified",
    "pedantic", "elevated", "scholarly"
}

TERSE_INDICATORS = {
    "laconic", "terse", "few words", "monosyllabic", "sparing with words",
    "curt", "taciturn"
}

THIRD_PERSON_INDICATORS = {
    "third person", "refers to self in third person", "refers to himself in third person",
    "refers to herself in third person"
}


@dataclass
class DialogueTurn:
    """A single dialogue turn extracted from prose with speaker attribution."""
    character: Optional[Character]
    speaker_name: str
    quote: str
    context_sentence: str
    start_pos: int
    end_pos: int


class DialogueExtractor:
    """Extracts quoted dialogue and attributes speech to scene characters using dialogue tags."""

    # Matches "...", “...”, or '...' dialogue patterns
    DIALOGUE_PATTERN = re.compile(
        r'(?:["“]([^"”]{2,})["”])|(?:\'([^\']{3,})\')'
    )

    @classmethod
    def extract_dialogue(cls, prose: str, characters: List[Character]) -> List[DialogueTurn]:
        """Finds all dialogue quotations and resolves speaker attribution."""
        turns: List[DialogueTurn] = []
        if not prose or not characters:
            return turns

        name_to_char = {}
        for c in characters:
            for v in c.get_name_variants():
                name_to_char[v.lower()] = c

        for m in cls.DIALOGUE_PATTERN.finditer(prose):
            quote = (m.group(1) or m.group(2) or "").strip()
            if len(quote) < 2:
                continue

            start, end = m.span()
            # Capture surrounding text window (up to 120 chars before and after)
            ctx_start = max(0, start - 120)
            ctx_end = min(len(prose), end + 120)
            surrounding = prose[ctx_start:ctx_end]

            # Look for dialogue tag pattern e.g. "said Alaric" or "Alaric said"
            attributed_char = None
            speaker_name = ""

            # 1. Search immediate post-quote tag (e.g. `," said Alaric` or `," said Lord Alaric`)
            post_window = prose[end:min(len(prose), end + 90)]
            post_tag_rx = re.compile(rf'^[,\s—-]*(?:{SPEECH_VERBS})\s+([A-Z][a-zA-Z\'-]+(?:\s+[A-Z][a-zA-Z\'-]+)?)', re.IGNORECASE)
            post_match = post_tag_rx.search(post_window)
            if post_match:
                tag_cand = post_match.group(1).strip().lower()
                for token in [tag_cand, tag_cand.split()[0], tag_cand.split()[-1]]:
                    if token in name_to_char:
                        attributed_char = name_to_char[token]
                        speaker_name = attributed_char.name
                        break

            # 2. Search post-quote `Name said` pattern
            if not attributed_char:
                post_tag_rx2 = re.compile(rf'^[,\s—-]+([A-Z][a-zA-Z\'-]+(?:\s+[A-Z][a-zA-Z\'-]+)?)\s+(?:{SPEECH_VERBS})', re.IGNORECASE)
                post_match2 = post_tag_rx2.search(post_window)
                if post_match2:
                    tag_cand2 = post_match2.group(1).strip().lower()
                    for token in [tag_cand2, tag_cand2.split()[0], tag_cand2.split()[-1]]:
                        if token in name_to_char:
                            attributed_char = name_to_char[token]
                            speaker_name = attributed_char.name
                            break

            # 3. Search pre-quote tag (e.g. `Alaric said, "..."`)
            if not attributed_char:
                pre_window = prose[max(0, start - 90):start]
                pre_tag_rx = re.compile(rf'([A-Z][a-zA-Z\'-]+(?:\s+[A-Z][a-zA-Z\'-]+)?)\s+(?:{SPEECH_VERBS})[,\s—:]+$', re.IGNORECASE)
                pre_match = pre_tag_rx.search(pre_window)
                if pre_match:
                    tag_cand_pre = pre_match.group(1).strip().lower()
                    for token in [tag_cand_pre, tag_cand_pre.split()[0], tag_cand_pre.split()[-1]]:
                        if token in name_to_char:
                            attributed_char = name_to_char[token]
                            speaker_name = attributed_char.name
                            break

            # 4. Fallback: match any character variant present in the immediate tag clause (before newline or period)
            if not attributed_char:
                # Isolate the immediate sentence clause containing the quote
                line_start = prose.rfind('\n', 0, start)
                line_start = 0 if line_start == -1 else line_start + 1
                line_end = prose.find('\n', end)
                line_end = len(prose) if line_end == -1 else line_end
                current_line = prose[line_start:line_end]

                for var_lower, char_obj in name_to_char.items():
                    if re.search(rf'\b{re.escape(var_lower)}\b', current_line.lower()):
                        attributed_char = char_obj
                        speaker_name = char_obj.name
                        break

            turns.append(
                DialogueTurn(
                    character=attributed_char,
                    speaker_name=speaker_name,
                    quote=quote,
                    context_sentence=surrounding.strip(),
                    start_pos=start,
                    end_pos=end,
                )
            )

        return turns


class DialogueVoiceAuditor:
    """Audits extracted character dialogue against their voice profile and genre conventions."""

    @classmethod
    def audit_turn(
        cls,
        turn: DialogueTurn,
        project_genre: str = "Fantasy",
    ) -> List[Dict[str, Any]]:
        """Audits a dialogue turn against the speaker's dialogue_style. Returns list of issue dicts."""
        issues: List[Dict[str, Any]] = []
        if not turn.character or not turn.character.dialogue_style:
            return issues

        style_lower = turn.character.dialogue_style.lower()
        quote_lower = turn.quote.lower()
        words = re.findall(r"\b[a-z']+\b", quote_lower)
        word_count = len(words)

        is_formal = any(ind in style_lower for ind in FORMAL_INDICATORS)
        is_terse = any(ind in style_lower for ind in TERSE_INDICATORS)
        is_third_person = any(ind in style_lower for ind in THIRD_PERSON_INDICATORS)
        is_historical_or_fantasy = any(g in project_genre.lower() for g in ["fantasy", "historical", "medieval", "xianxia", "wuxia"])

        # 1. Check informal slang / contractions in formal speech
        if is_formal:
            found_informals = []
            for inf, formal_alt in INFORMAL_CONTRACTIONS.items():
                if re.search(rf"\b{re.escape(inf)}\b", quote_lower):
                    found_informals.append(f"'{inf}' (suggest: '{formal_alt}')")
            if found_informals:
                issues.append({
                    "type": "formality_clash",
                    "claim": (
                        f"Out-of-character informal speech for {turn.character.name}: "
                        f"Used {', '.join(found_informals[:3])}, contradicting formal dialogue style."
                    ),
                    "evidence": f"Character dialogue_style: '{turn.character.dialogue_style}'",
                    "suggestion": f"Elevate cadence to match formal register (replace informal contractions).",
                })

        # 2. Check anachronisms in historical/fantasy settings
        if is_historical_or_fantasy:
            found_anachronisms = []
            for anach, replacement in ANACHRONISMS.items():
                if re.search(rf"\b{re.escape(anach)}\b", quote_lower):
                    found_anachronisms.append(f"'{anach}' (suggest: '{replacement}')")
            if found_anachronisms:
                issues.append({
                    "type": "anachronism",
                    "claim": (
                        f"Modern anachronism in dialogue spoken by {turn.character.name}: "
                        f"Used {', '.join(found_anachronisms[:3])} in a {project_genre} setting."
                    ),
                    "evidence": f"Genre: {project_genre} | Quote: \"{turn.quote}\"",
                    "suggestion": f"Replace modern jargon with world-appropriate terminology.",
                })

        # 3. Check third-person self-reference requirement
        if is_third_person:
            first_person_pronouns = {"i", "me", "my", "mine", "myself"}
            used_pronouns = [p for p in words if p in first_person_pronouns]
            if used_pronouns:
                issues.append({
                    "type": "pronoun_violation",
                    "claim": (
                        f"Dialogue constraint violation: {turn.character.name} used 1st-person pronoun ('{used_pronouns[0]}'), "
                        f"violating established style: '{turn.character.dialogue_style}'."
                    ),
                    "evidence": f"Character dialogue_style: '{turn.character.dialogue_style}'",
                    "suggestion": f"Rephrase line so {turn.character.name} refers to self in third person or by name.",
                })

        # 4. Check terse/laconic cadence violation
        if is_terse and word_count > 45:
            issues.append({
                "type": "terse_violation",
                "claim": (
                    f"Dialogue cadence mismatch: {turn.character.name} gave a verbose speech ({word_count} words), "
                    f"conflicting with laconic/terse dialogue style."
                ),
                "evidence": f"Character dialogue_style: '{turn.character.dialogue_style}' (Word count: {word_count})",
                "suggestion": f"Condense dialogue into terse, punchy statements.",
            })

        return issues
