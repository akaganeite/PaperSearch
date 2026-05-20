import unittest

from papersearch.sources.venue_preprints import parse_page


class VenuePreprintSourceTests(unittest.TestCase):
    def test_parses_researchr_style_table_row(self):
        html = """
        <table>
          <tr>
            <td>VulnFix: LLM-Assisted Static Analysis for Vulnerability Repair</td>
            <td>Research Track</td>
            <td>Alice Example; Bob Example</td>
            <td><a href="https://example.test/vulnfix.pdf">Pre-print</a></td>
          </tr>
        </table>
        """
        papers = parse_page(
            html,
            page_url="https://conf.researchr.org/track/icse-2026/icse-2026-research-track",
            source_label="VenuePreprints",
            venue="ICSE",
            year=2026,
        )
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "VulnFix: LLM-Assisted Static Analysis for Vulnerability Repair")
        self.assertEqual(papers[0]["url"], "https://example.test/vulnfix.pdf")
        self.assertEqual(papers[0]["pdf_url"], "https://example.test/vulnfix.pdf")

    def test_parses_heading_and_doi_link(self):
        html = """
        <h3>Root Cause Analysis of Security Patches in Linux Kernel Drivers</h3>
        <p><a href="https://doi.org/10.1145/1234567.8901234">DOI</a></p>
        """
        papers = parse_page(
            html,
            page_url="https://www.sigsac.org/ccs/CCS2026/accepted-papers/",
            source_label="VenuePreprints",
            venue="CCS",
            year=2026,
        )
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["doi"], "")
        self.assertEqual(papers[0]["venue"], "CCS")


if __name__ == "__main__":
    unittest.main()
