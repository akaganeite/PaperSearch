import unittest
from unittest.mock import patch

from papersearch.pdf_resolver import (
    arxiv_pdf_candidate,
    publisher_access_pdf_url,
    publisher_source_for_url,
    resolve_pdf_for_paper,
    title_similarity,
)


class PdfResolverTests(unittest.TestCase):
    def test_arxiv_pdf_candidate_from_source_id(self):
        paper = {
            "sources": ["arXiv"],
            "source_id": "2605.12345v1",
            "url": "https://arxiv.org/abs/2605.12345v1",
        }
        self.assertEqual(arxiv_pdf_candidate(paper), "https://arxiv.org/pdf/2605.12345v1.pdf")

    def test_title_similarity_accepts_minor_punctuation_changes(self):
        self.assertGreater(
            title_similarity(
                "LLM-Assisted Static Analysis for Vulnerability Detection",
                "LLM Assisted Static Analysis for Vulnerability Detection",
            ),
            0.95,
        )

    def test_publisher_source_for_acm_and_ieee(self):
        self.assertEqual(publisher_source_for_url("https://dl.acm.org/doi/10.1145/123"), "ACM DL")
        self.assertEqual(publisher_source_for_url("https://ieeexplore.ieee.org/document/123"), "IEEE Xplore")

    def test_publisher_access_pdf_url_for_acm(self):
        result = publisher_access_pdf_url(
            "10.1145/3691620.3695482",
            "https://dl.acm.org/doi/10.1145/3691620.3695482",
        )
        self.assertEqual(result["publisher_pdf_url"], "https://dl.acm.org/doi/pdf/10.1145/3691620.3695482")

    def test_resolve_prefers_acm_access_before_external_providers(self):
        paper = {
            "title": "Example ACM Paper",
            "doi": "10.1145/3691620.3695482",
            "sources": [],
            "source_id": "",
            "url": "",
        }
        config = {"pdf_resolver": {"request_delay_seconds": 0, "unpaywall_email": "test@example.com"}}

        with (
            patch("papersearch.pdf_resolver.verify_pdf_url", return_value=""),
            patch("papersearch.pdf_resolver._semantic_scholar_candidates") as semantic_scholar,
            patch("papersearch.pdf_resolver._openalex_candidates") as openalex,
            patch("papersearch.pdf_resolver._crossref_candidates") as crossref,
            patch("papersearch.pdf_resolver._unpaywall_candidates") as unpaywall,
        ):
            result = resolve_pdf_for_paper(paper, config)

        self.assertEqual(result["publisher_pdf_source"], "ACM DL")
        self.assertEqual(result["publisher_pdf_url"], "https://dl.acm.org/doi/pdf/10.1145/3691620.3695482")
        semantic_scholar.assert_not_called()
        openalex.assert_not_called()
        crossref.assert_not_called()
        unpaywall.assert_not_called()


if __name__ == "__main__":
    unittest.main()
