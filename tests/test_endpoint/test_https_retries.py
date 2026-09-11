"""Exercise endpoint retries against real local HTTP and HTTPS servers."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import ipaddress
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from runpod.endpoint.runner import RunPodClient


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_endpoint_get_retries_rate_limit_over_both_schemes(tmp_path, scheme):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(429 if len(requests) == 1 else 200)
            body = b'{"status":"COMPLETED"}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    client = RunPodClient(api_key="test-key")
    client.rp_session.trust_env = False
    if scheme == "https":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        certificate_path = tmp_path / "certificate.pem"
        key_path = tmp_path / "key.pem"
        certificate_path.write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
        )
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate_path, key_path)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        client.rp_session.verify = str(certificate_path)

    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}
    )
    thread.start()
    client.endpoint_url_base = f"{scheme}://127.0.0.1:{server.server_port}"
    try:
        assert client.get("endpoint/status/job") == {"status": "COMPLETED"}
        assert requests == ["/endpoint/status/job", "/endpoint/status/job"]
    finally:
        client.rp_session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
