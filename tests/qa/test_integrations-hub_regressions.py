"""QA regressions for bugs found while testing the Integrations hub (each fixed in its own commit)."""
from app.channels.custom_http import RESPONSE_PREVIEW_BYTES, response_preview


def test_reply_preview_is_cut_at_2kb_even_for_hindi_text():
    """Regression: the cut counted characters, so a Devanagari reply showed ~6 KB instead of the documented 2 KB."""
    text = response_preview({"message": "संदेश भेजा गया " * 400})
    shown = text.removesuffix("… (cut)")
    assert text.endswith("… (cut)")
    assert len(shown.encode("utf-8")) <= RESPONSE_PREVIEW_BYTES
    assert len(shown.encode("utf-8")) > RESPONSE_PREVIEW_BYTES - 4                # cut on a character boundary
    assert "�" not in shown
