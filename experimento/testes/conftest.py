"""Sobe o barramento (infra) e os cinco serviços do ALVO em portas livres, como processos separados.

ALVO é a pasta que contém servicos/<servico>/main.py (padrão: a implementação de referência).
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

RAIZ = Path(__file__).resolve().parent.parent
ALVO = Path(os.environ.get("ALVO", RAIZ / "referencia")).resolve()
SERVICOS = ["transportadoras", "cotacao", "envios", "rastreamento", "notificacoes"]


def _porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Sistema:
    def __init__(self):
        self.urls = {nome: f"http://127.0.0.1:{_porta_livre()}" for nome in SERVICOS + ["barramento"]}
        self.pasta_logs = Path(tempfile.mkdtemp(prefix="logistica-"))
        self.saidas = {}
        self.processos = {}
        self.cliente = httpx.Client()

    def _ambiente(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("URL_")}
        env.update({f"URL_{nome.upper()}": url for nome, url in self.urls.items()})
        env["ASSINANTES"] = f"{self.urls['rastreamento']},{self.urls['notificacoes']}"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def _iniciar(self, nome):
        pasta, app = (RAIZ / "infra", "barramento:app") if nome == "barramento" else (ALVO, f"servicos.{nome}.main:app")
        porta = self.urls[nome].rsplit(":", 1)[1]
        self.saidas[nome] = self.pasta_logs / f"{nome}.out"
        cmd = [sys.executable, "-m", "uvicorn", app, "--app-dir", str(pasta), "--host", "127.0.0.1",
               "--port", porta, "--no-access-log", "--log-level", "warning"]
        self.processos[nome] = subprocess.Popen(
            cmd, cwd=str(pasta), env=self._ambiente(),
            stdout=open(self.saidas[nome], "ab"), stderr=open(self.pasta_logs / f"{nome}.err", "ab"),
        )

    def _esperar(self, nomes, segundos=30):
        limite = time.time() + segundos
        pendentes = set(nomes)
        while pendentes and time.time() < limite:
            for nome in list(pendentes):
                if self.processos[nome].poll() is not None:
                    pendentes.discard(nome)
                    continue
                try:
                    self.cliente.get(f"{self.urls[nome]}/openapi.json", timeout=1)
                    pendentes.discard(nome)
                except httpx.HTTPError:
                    pass
            if pendentes:
                time.sleep(0.2)

    def subir(self):
        for nome in self.urls:
            self._iniciar(nome)
        self._esperar(self.urls)

    def reiniciar(self):
        """Reinicia o estado pelo POST /admin/reset; se a rota falhar, reinicia o processo do serviço."""
        reiniciar_processo = []
        for nome, url in self.urls.items():
            try:
                ok = self.cliente.post(f"{url}/admin/reset", timeout=5).status_code == 204
            except httpx.HTTPError:
                ok = False
            if not ok:
                reiniciar_processo.append(nome)
        for nome in reiniciar_processo:
            p = self.processos[nome]
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
            self._iniciar(nome)
        if reiniciar_processo:
            self._esperar(reiniciar_processo)

    def derrubar(self):
        for p in self.processos.values():
            p.terminate()
        for p in self.processos.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


@pytest.fixture(scope="session")
def sistema():
    s = Sistema()
    s.subir()
    yield s
    s.derrubar()


@pytest.fixture(autouse=True)
def _limpo(sistema):
    sistema.reiniciar()
    yield
