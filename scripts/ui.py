#!/usr/bin/env python3
"""Local form in front of the scraper API: city name + radius in km instead of raw coordinates.

    python3 scripts/ui.py            # then open http://127.0.0.1:8090

Standard library only. Binds to 127.0.0.1 — like the scraper itself, it has no authentication.
The city is resolved with OpenStreetMap Nominatim (free, ~1 request/sec; fine for a form).
"""
import csv, functools, io, json, os, re, sys, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jobform import build_job_body, complete_rows, leads_within_radius
from scrape import LEAD, UA, req

HOST = "127.0.0.1"
PORT = int(os.environ.get("GMAPS_UI_PORT", "8090"))
PAGE = Path(__file__).resolve().parent / "ui.html"
VIEW_PAGE = Path(__file__).resolve().parent / "view.html"
FIELDS = LEAD + ["distance_km"]
MAX_BODY_BYTES = 64_000
JOB_ID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
SCRAPER_DOWN = "Scraper fora do ar. Rode 'docker compose up -d' na pasta do kit."


class BadRequest(Exception):
    pass


def geocode(place):
    """Place name -> {lat, lon, display_name}, or None when nothing matches."""
    query = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place, "accept-language": "pt-BR"})
    request = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{query}",
                                     headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as resp:
        hits = json.loads(resp.read())
    if not hits:
        return None
    return {"lat": float(hits[0]["lat"]), "lon": float(hits[0]["lon"]),
            "display_name": hits[0].get("display_name", place)}


def find_job(job_id):
    _, raw = req("GET", f"/api/v1/jobs/{job_id}")
    return json.loads(raw)


def job_rows(job):
    """Raw result rows. Partial while the job runs; empty until the scraper writes the first one."""
    if job.get("Status") == "pending":
        return []
    try:
        _, raw = req("GET", f"/api/v1/jobs/{job['ID']}/download")
    except urllib.error.HTTPError as e:
        if e.code == 404:  # results file not created yet
            return []
        raise
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
    return complete_rows(rows, job.get("Status") != "ok")


def job_leads(job):
    """Job results so far, trimmed to lead fields and to the job's radius."""
    rows = job_rows(job)
    data = job["Data"]
    return leads_within_radius(rows, LEAD, data["lat"], data["lon"], data["radius"])


@functools.lru_cache(maxsize=256)
def finished_lead_count(job_id):
    """Lead count of a finished job. Cached: its results no longer change."""
    return len(job_leads(find_job(job_id)))


def lead_count(job):
    """How many leads the job's CSV holds right now, or None when it cannot be read."""
    try:
        if job.get("Status") == "ok":
            return finished_lead_count(job["ID"])
        return len(job_leads(job))
    except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
        print(f"could not count leads of job {job.get('ID')}: {e}", file=sys.stderr)
        return None


def leads_csv(leads):
    """Leads as Excel-friendly CSV bytes."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(leads)
    return out.getvalue().encode("utf-8-sig")  # BOM so Excel reads the accents


def csv_filename(job):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", job.get("Name") or "").strip("-").lower()
    return f"{slug or 'leads'}-{job['ID'][:8]}.csv"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data=None, error=None):
        envelope = {"success": error is None, "data": data, "error": error}
        self._send(status, json.dumps(envelope).encode(), "application/json; charset=utf-8")

    def _read_json(self):
        # Requiring a JSON content type forces a CORS preflight, so other sites open in the
        # browser cannot post jobs to this server.
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            raise BadRequest("Envie o corpo como application/json.")
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY_BYTES:
            raise BadRequest("Corpo da requisição vazio ou grande demais.")
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            raise BadRequest("JSON inválido.")
        if not isinstance(payload, dict):
            raise BadRequest("JSON inválido.")
        return payload

    def _dispatch(self, routes):
        # Only answer requests addressed to localhost (blocks DNS-rebinding from a web page).
        if (self.headers.get("Host") or "").split(":")[0] not in ("127.0.0.1", "localhost"):
            return self._json(403, error="Acesso permitido apenas via localhost.")
        url = urllib.parse.urlsplit(self.path)
        try:
            for pattern, action in routes:
                match = re.fullmatch(pattern, url.path)
                if match:
                    return action(urllib.parse.parse_qs(url.query), *match.groups())
            self._json(404, error="Rota não encontrada.")
        except (BadRequest, ValueError) as e:
            self._json(400, error=str(e))
        except urllib.error.HTTPError as e:
            if e.code == 404 and "/jobs/" in url.path:
                return self._json(404, error="Job não encontrado. Ele pode ter sido excluído.")
            detail = e.read().decode("utf-8", "replace")[:300]
            print(f"upstream HTTP {e.code} on {self.path}: {detail}", file=sys.stderr)
            self._json(502, error=f"O serviço respondeu com erro (HTTP {e.code}). {detail}")
        except (urllib.error.URLError, OSError) as e:
            print(f"upstream unreachable on {self.path}: {e}", file=sys.stderr)
            self._json(502, error=SCRAPER_DOWN if "/jobs" in url.path else f"Falha de rede: {e}")

    def do_GET(self):
        self._dispatch([(r"/", self.page), (r"/api/geocode", self.geocode),
                        (r"/api/jobs", self.list_jobs),
                        (rf"/jobs/({JOB_ID})", self.view_page),
                        (rf"/api/jobs/({JOB_ID})/leads", self.leads),
                        (rf"/api/jobs/({JOB_ID})/download", self.download)])

    def do_POST(self):
        self._dispatch([(r"/api/jobs", self.create_job)])

    def do_DELETE(self):
        self._dispatch([(rf"/api/jobs/({JOB_ID})", self.delete_job)])

    def page(self, _query):
        self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")

    def geocode(self, query):
        place = (query.get("q") or [""])[0].strip()
        if not place:
            raise BadRequest("Cidade: informe o nome da cidade.")
        hit = geocode(place)
        if not hit:
            return self._json(404, error=f"Não encontrei \"{place}\". Tente \"Cidade, UF\".")
        self._json(200, hit)

    def list_jobs(self, _query):
        _, raw = req("GET", "/api/v1/jobs")
        self._json(200, [{**job, "lead_count": lead_count(job)} for job in json.loads(raw) or []])

    def create_job(self, _query):
        body = build_job_body(self._read_json())
        _, raw = req("POST", "/api/v1/jobs", body)
        self._json(201, {"id": json.loads(raw).get("id"), "job": body})

    def view_page(self, _query, _job_id):
        self._send(200, VIEW_PAGE.read_bytes(), "text/html; charset=utf-8")

    def leads(self, _query, job_id):
        job = find_job(job_id)
        self._json(200, {"job": job, "fields": FIELDS, "leads": job_leads(job)})

    def download(self, _query, job_id):
        job = find_job(job_id)
        leads = job_leads(job)
        if not leads:
            raise BadRequest("Esse job ainda não tem resultados para baixar.")
        self._send(200, leads_csv(leads), "text/csv; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="{csv_filename(job)}"'})

    def delete_job(self, _query, job_id):
        req("DELETE", f"/api/v1/jobs/{job_id}")
        self._json(200, {"id": job_id})

    def log_message(self, fmt, *args):
        if "/api/jobs HTTP" not in (args[0] if args else ""):  # skip the 5s polling noise
            super().log_message(fmt, *args)


def main():
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        sys.exit(f"✗ Não consegui abrir {HOST}:{PORT} ({e}). Use GMAPS_UI_PORT=<porta> para trocar.")
    print(f"▶ Formulário em http://{HOST}:{PORT}  (Ctrl+C para parar)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n■ Parado.")


if __name__ == "__main__":
    main()
