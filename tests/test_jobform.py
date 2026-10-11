"""Run: python3 -m unittest discover -s tests"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import jobform

SAO_PAULO = (-23.5506507, -46.6333824)
VALID = {"keywords": "dentistas\nclínica odontológica", "city": "Curitiba, PR", "lat": -25.43,
         "lon": -49.27, "radius_km": 5, "lang": "pt", "depth": 5, "email": True, "max_minutes": 10}


class ParseKeywordsTest(unittest.TestCase):
    def test_splits_one_keyword_per_line(self):
        self.assertEqual(jobform.parse_keywords("dentistas\nortodontista"), ["dentistas", "ortodontista"])

    def test_keeps_commas_inside_a_line_as_one_keyword(self):
        self.assertEqual(jobform.parse_keywords("dentistas, ortodontista"), ["dentistas, ortodontista"])

    def test_drops_blanks_comments_and_case_insensitive_duplicates(self):
        text = "  dentistas  \n\n# nota\nDentistas\r\nacademia"
        self.assertEqual(jobform.parse_keywords(text), ["dentistas", "academia"])

    def test_returns_empty_list_for_missing_text(self):
        self.assertEqual(jobform.parse_keywords(None), [])


class GeoMathTest(unittest.TestCase):
    def test_converts_km_to_whole_meters(self):
        self.assertEqual(jobform.km_to_m("2.5"), 2500)

    def test_zooms_out_as_radius_grows(self):
        zooms = [jobform.zoom_for_radius(km, SAO_PAULO[0]) for km in (1, 10, 50)]
        self.assertEqual(zooms, sorted(zooms, reverse=True))
        self.assertEqual(jobform.zoom_for_radius(10, SAO_PAULO[0]), 13)

    def test_clamps_zoom_to_supported_range(self):
        self.assertEqual(jobform.zoom_for_radius(0.01, 0), jobform.MAX_ZOOM)
        self.assertEqual(jobform.zoom_for_radius(5000, 0), jobform.MIN_ZOOM)

    def test_measures_sao_paulo_to_rio_distance(self):
        distance = jobform.haversine_km(*SAO_PAULO, -22.9068, -43.1729)
        self.assertAlmostEqual(distance, 361, delta=3)


class BuildJobBodyTest(unittest.TestCase):
    def test_builds_api_body_from_friendly_fields(self):
        body = jobform.build_job_body(VALID)
        self.assertEqual(body["keywords"], ["dentistas", "clínica odontológica"])
        self.assertEqual((body["lat"], body["lon"]), ("-25.43", "-49.27"))
        self.assertEqual((body["radius"], body["max_time"], body["lang"]), (5000, 600, "pt"))
        self.assertEqual(body["name"], "dentistas — Curitiba, PR")

    def test_defaults_language_to_portuguese(self):
        self.assertEqual(jobform.build_job_body({**VALID, "lang": ""})["lang"], "pt")

    def test_rejects_unknown_language(self):
        with self.assertRaisesRegex(ValueError, "Idioma"):
            jobform.build_job_body({**VALID, "lang": "xx"})

    def test_rejects_empty_keywords(self):
        with self.assertRaisesRegex(ValueError, "Palavras-chave"):
            jobform.build_job_body({**VALID, "keywords": " \n# só comentário"})

    def test_rejects_radius_out_of_range_or_not_numeric(self):
        for bad in (0, 500, "abc", None):
            with self.assertRaisesRegex(ValueError, "Raio"):
                jobform.build_job_body({**VALID, "radius_km": bad})

    def test_rejects_missing_coordinates(self):
        with self.assertRaisesRegex(ValueError, "Latitude"):
            jobform.build_job_body({**VALID, "lat": None})


class CompleteRowsTest(unittest.TestCase):
    FULL = {"title": "A", "emails": ""}
    TRUNCATED = {"title": "B", "emails": None}

    def test_drops_truncated_last_row_while_job_is_running(self):
        self.assertEqual(jobform.complete_rows([self.FULL, self.TRUNCATED], True), [self.FULL])

    def test_keeps_complete_rows_while_job_is_running(self):
        self.assertEqual(jobform.complete_rows([self.FULL, self.FULL], True), [self.FULL, self.FULL])

    def test_keeps_every_row_of_a_finished_job(self):
        rows = [self.FULL, self.TRUNCATED]
        self.assertEqual(jobform.complete_rows(rows, False), rows)

    def test_handles_empty_results(self):
        self.assertEqual(jobform.complete_rows([], True), [])


class LeadsWithinRadiusTest(unittest.TestCase):
    ROWS = [{"title": "Perto", "latitude": "-23.5510", "longitude": "-46.6340", "cid": "1"},
            {"title": "Longe", "latitude": "-22.9068", "longitude": "-43.1729", "cid": "2"},
            {"title": "Sem coordenada", "latitude": "", "longitude": "", "cid": "3"}]

    def test_drops_rows_outside_radius_and_keeps_rows_without_coordinates(self):
        leads = jobform.leads_within_radius(self.ROWS, ["title"], *SAO_PAULO, 10000)
        self.assertEqual([lead["title"] for lead in leads], ["Perto", "Sem coordenada"])

    def test_keeps_only_requested_fields_plus_distance(self):
        leads = jobform.leads_within_radius(self.ROWS, ["title"], *SAO_PAULO, 10000)
        self.assertEqual(leads[0], {"title": "Perto", "distance_km": "0.1"})
        self.assertEqual(leads[1]["distance_km"], "")


if __name__ == "__main__":
    unittest.main()
