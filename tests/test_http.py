import json
import threading
from http.client import HTTPConnection

from frameatlas.coverage import Coverage
from frameatlas.server import make_server


def test_viewer_routes_and_security(dataset):
    with make_server(dataset, 0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            client.request("GET", "/")
            response = client.getresponse()
            assert response.status == 200
            assert b"Frame explorer" in response.read()
            client.request("GET", "/api/frames?start=16&count=8")
            response = client.getresponse()
            assert json.loads(response.read())[0]["index"] == 16
            client.request("HEAD", "/api/image/0")
            response = client.getresponse()
            assert response.status == 200
            assert response.read() == b""
            assert Coverage(dataset).report("viewer")["native_images_delivered"] == 0
            client.request("GET", "/api/thumbnail/0")
            response = client.getresponse()
            assert response.status == 200
            response.read()
            client.request("GET", "/api/sheet?start=0&count=8")
            response = client.getresponse()
            assert response.status == 200
            response.read()
            assert Coverage(dataset).report("viewer")["native_images_delivered"] == 0
            client.request("GET", "/api/image/0")
            response = client.getresponse()
            assert response.status == 200
            assert response.read().startswith(b"\x89PNG")
            # A follow-up coverage request is after response-body delivery accounting.
            client.request("GET", "/api/coverage")
            response = client.getresponse()
            assert json.loads(response.read())["native_images_delivered"] == 1
            for path, status in [("/../manifest.json",404),("/api/image/999",400),("/api/frames?count=201",400),("/api/frames?start=0&start=1",400),("/api/frames?count=NaN",400)]:
                client.request("GET", path)
                response = client.getresponse()
                assert response.status == status
                response.read()
            for headers in [{"Host":"evil.example"},{"Origin":"https://evil.example"},{"Sec-Fetch-Site":"cross-site"}]:
                client.request("GET", "/api/info", headers=headers)
                response = client.getresponse()
                assert response.status == 403
                response.read()
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=5)
