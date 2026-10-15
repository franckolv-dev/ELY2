"""Envoi de fichier par l'extension, sans navigateur réel."""
from __future__ import annotations

from ely import chrome


class FakePage:
    """Onglet dont la page retire le champ de fichier dès qu'il reçoit un fichier (comme Google Drive)."""

    def __init__(self) -> None:
        self.field_present = True
        self.calls: list[str] = []

    async def cdp(self, method, timeout=30, **params):
        self.calls.append(method)
        if method == "Runtime.evaluate":
            return {"result": {"objectId": "champ"}}
        if method == "DOM.setFileInputFiles":
            self.field_present = False
        return {}

    async def evaluate(self, fn, arg=None):
        self.calls.append("evaluate")
        if "retiré" in fn:
            return None if self.field_present else "retiré"
        if not self.field_present:
            raise chrome.ChromeError(f"élément [{arg}] introuvable : refais un snapshot")
        return None


async def test_upload_consumed_by_the_page_is_a_success_and_is_not_sent_twice(tmp_path):
    f = tmp_path / "facture.pdf"
    f.write_bytes(b"%PDF-1.7 facture")
    page = FakePage()
    await chrome.ChromeLocator(page, "52").set_input_files(str(f))
    assert page.calls.count("DOM.setFileInputFiles") == 1
    assert page.calls.count("evaluate") == 1, "aucun second dépôt du fichier par la liaison"
