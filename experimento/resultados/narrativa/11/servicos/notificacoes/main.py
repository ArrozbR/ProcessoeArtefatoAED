# arquivo: servicos/notificacoes/main.py
import threading
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


app = FastAPI(title="Notificacoes")


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    partes = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()))
        partes.append(f"{loc}: {e.get('msg', '')}")
    return _erro(422, "DADOS_INVALIDOS", "; ".join(partes) or "Dados invalidos.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_trava = threading.RLock()
_notificacoes: list = []
_eventos_processados: set = set()
_contador = {"notificacao": 0}


class Evento(BaseModel):
    id: Annotated[str, Field(strict=True)]
    tipo: Annotated[str, Field(strict=True)]
    ocorrido_em: Annotated[str, Field(strict=True)]
    dados: dict


def _exigir(dados: dict, chaves) -> None:
    faltando = [c for c in chaves if c not in dados]
    if faltando:
        raise ErroAPI(422, "DADOS_INVALIDOS", "Campos ausentes em dados: " + ", ".join(faltando))


def _registrar(dados: dict, tipo: str, mensagem: str, criada_em: str) -> None:
    _contador["notificacao"] += 1
    _notificacoes.append({
        "id": f"NOT-{_contador['notificacao']:06d}",
        "email": dados["destinatario_email"],
        "pedido_id": dados["pedido_id"],
        "codigo_rastreio": dados["codigo_rastreio"],
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": criada_em,
    })


@app.post("/admin/reset", status_code=204)
def reset():
    with _trava:
        _notificacoes.clear()
        _eventos_processados.clear()
        _contador["notificacao"] = 0
    return Response(status_code=204)


@app.post("/eventos")
def receber_evento(evento: Evento):
    with _trava:
        if evento.id in _eventos_processados:
            return {"processado": False}
        dados = evento.dados
        if evento.tipo == "envio.criado":
            _exigir(dados, ["pedido_id", "codigo_rastreio", "destinatario_email"])
            mensagem = f"Pedido {dados['pedido_id']}: envio criado. Rastreio: {dados['codigo_rastreio']}."
            _registrar(dados, "CRIADO", mensagem, evento.ocorrido_em)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        if evento.tipo == "envio.status_atualizado":
            _exigir(dados, ["status"])
            status = dados["status"]
            if status in MENSAGENS_STATUS:
                _exigir(dados, ["pedido_id", "codigo_rastreio", "destinatario_email"])
                mensagem = MENSAGENS_STATUS[status].format(pedido_id=dados["pedido_id"])
                _registrar(dados, status, mensagem, evento.ocorrido_em)
            _eventos_processados.add(evento.id)
            return {"processado": True}
        return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    with _trava:
        lista = [dict(n) for n in _notificacoes if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return lista
