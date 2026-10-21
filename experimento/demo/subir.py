"""Sobe o módulo de logística para demonstração.

Uso (dentro de experimento/):
    .venv\\Scripts\\python demo\\subir.py                       # código gerado pela LLM (resultados/erl/01)
    .venv\\Scripts\\python demo\\subir.py --alvo referencia     # implementação de referência
    .venv\\Scripts\\python demo\\subir.py --alvo resultados/narrativa/07

Depois abra http://127.0.0.1:8000 (página) ou o Swagger de cada serviço (endereços impressos no terminal).
Ctrl+C derruba tudo.
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse

RAIZ = Path(__file__).resolve().parent.parent
SERVICOS = {
    "transportadoras": 8101,
    "cotacao": 8102,
    "envios": 8103,
    "rastreamento": 8104,
    "notificacoes": 8105,
}
BARRAMENTO = 8100
URLS = {nome: f"http://127.0.0.1:{porta}" for nome, porta in SERVICOS.items()}
URLS["barramento"] = f"http://127.0.0.1:{BARRAMENTO}"

TRANSPORTADORAS = [
    {"id": "RAPIDA", "nome": "Rápida Log", "ufs_atendidas": ["SP", "RJ", "MG"],
     "tarifa_base_centavos": 1500, "tarifa_kg_centavos": 300, "adicional_interestadual_pct": 20,
     "prazo_dias_uteis": 2, "dias_adicionais_interestadual": 2, "peso_maximo_g": 30000},
    {"id": "ECONOMICA", "nome": "Econômica Express", "ufs_atendidas": ["SP", "RJ", "MG", "PR", "SC", "RS"],
     "tarifa_base_centavos": 900, "tarifa_kg_centavos": 200, "adicional_interestadual_pct": 10,
     "prazo_dias_uteis": 5, "dias_adicionais_interestadual": 3, "peso_maximo_g": 50000},
    {"id": "EXPRESSA", "nome": "Expressa Já", "ufs_atendidas": ["SP"],
     "tarifa_base_centavos": 2500, "tarifa_kg_centavos": 100, "adicional_interestadual_pct": 0,
     "prazo_dias_uteis": 1, "dias_adicionais_interestadual": 0, "peso_maximo_g": 10000},
    {"id": "SULOG", "nome": "SuLog Transportes", "ufs_atendidas": ["SP", "PR", "SC", "RS"],
     "tarifa_base_centavos": 1200, "tarifa_kg_centavos": 250, "adicional_interestadual_pct": 15,
     "prazo_dias_uteis": 3, "dias_adicionais_interestadual": 2, "peso_maximo_g": 40000},
]

app = FastAPI(title="Gateway da demonstração")
cliente = httpx.Client(timeout=15)


@app.get("/")
def pagina():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/status")
def status():
    estado = {}
    for nome, url in URLS.items():
        try:
            cliente.get(f"{url}/openapi.json", timeout=1)
            estado[nome] = True
        except httpx.HTTPError:
            estado[nome] = False
    return estado


@app.post("/api/demo/reset")
def reset():
    for url in URLS.values():
        try:
            cliente.post(f"{url}/admin/reset")
        except httpx.HTTPError:
            pass
    for t in TRANSPORTADORAS:
        cliente.post(f"{URLS['transportadoras']}/transportadoras", json=t)
    return {"ok": True}


@app.api_route("/api/{servico}/{caminho:path}", methods=["GET", "POST", "PATCH"])
async def repassar(servico: str, caminho: str, request: Request):
    if servico not in URLS:
        return JSONResponse({"erro": "SERVICO_DESCONHECIDO"}, status_code=404)
    corpo = await request.body()
    try:
        r = cliente.request(request.method, f"{URLS[servico]}/{caminho}", params=request.query_params,
                            content=corpo, headers={"content-type": "application/json"})
    except httpx.HTTPError:
        return JSONResponse({"erro": "SERVICO_INDISPONIVEL", "mensagem": servico}, status_code=503)
    return Response(r.content, status_code=r.status_code, media_type="application/json")


def subir_servicos(alvo):
    env = {k: v for k, v in os.environ.items() if not k.startswith("URL_")}
    env.update({f"URL_{nome.upper()}": url for nome, url in URLS.items()})
    env["ASSINANTES"] = f"{URLS['rastreamento']},{URLS['notificacoes']}"
    processos = []
    alvos = [(RAIZ / "infra", "barramento:app", BARRAMENTO)]
    alvos += [(alvo, f"servicos.{nome}.main:app", porta) for nome, porta in SERVICOS.items()]
    for pasta, modulo, porta in alvos:
        processos.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", modulo, "--app-dir", str(pasta), "--host", "127.0.0.1",
             "--port", str(porta), "--no-access-log", "--log-level", "warning"],
            cwd=str(pasta), env=env,
        ))
    limite = time.time() + 30
    while time.time() < limite and not all(status().values()):
        time.sleep(0.3)
    return processos


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alvo", default="resultados/erl/01",
                   help="pasta que contém servicos/ (relativa a experimento/)")
    args = p.parse_args()
    alvo = (RAIZ / args.alvo).resolve()
    if not (alvo / "servicos").exists():
        sys.exit(f"Não encontrei {alvo / 'servicos'}")
    processos = subir_servicos(alvo)
    try:
        estado = status()
        if not all(estado.values()):
            print("Atenção, serviços que não subiram:", [n for n, ok in estado.items() if not ok])
        reset()
        print(f"\nCódigo em uso: {alvo}")
        print("Página da demonstração:  http://127.0.0.1:8000")
        for nome, url in URLS.items():
            print(f"Swagger {nome:<16} {url}/docs")
        print("\nCtrl+C para encerrar.\n")
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    finally:
        for proc in processos:
            proc.terminate()


if __name__ == "__main__":
    main()
