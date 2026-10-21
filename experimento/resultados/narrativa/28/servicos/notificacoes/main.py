# arquivo: servicos/notificacoes/main.py
import json

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS_STATUS = {
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def tratar_erro_api(request: Request, exc: ErroApi):
    return resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def tratar_validacao(request: Request, exc: RequestValidationError):
    return resposta_erro(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


@app.exception_handler(StarletteHTTPException)
async def tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return resposta_erro(404, "NAO_ENCONTRADO", "Recurso nao encontrado.")
    if exc.status_code == 405:
        return resposta_erro(405, "METODO_NAO_PERMITIDO", "Metodo nao permitido.")
    return resposta_erro(exc.status_code, "ERRO", str(exc.detail))


notificacoes = []
eventos_processados = set()
estado = {"contador": 0}


def dados_invalidos():
    return ErroApi(422, "DADOS_INVALIDOS", "Dados invalidos na requisicao.")


async def ler_evento(request: Request) -> dict:
    try:
        corpo = await request.body()
        evento = json.loads(corpo)
    except Exception:
        raise dados_invalidos()
    if not isinstance(evento, dict):
        raise dados_invalidos()
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento or evento[campo] is None:
            raise dados_invalidos()
    if not isinstance(evento["dados"], dict):
        raise dados_invalidos()
    if not isinstance(evento["id"], (str, int)) or not isinstance(evento["tipo"], str):
        raise dados_invalidos()
    return evento


def registrar_notificacao(evento: dict, tipo: str, mensagem: str):
    dados = evento["dados"]
    numero = estado["contador"] + 1
    estado["contador"] = numero
    notificacoes.append({
        "id": "NOT-%06d" % numero,
        "email": dados.get("destinatario_email"),
        "pedido_id": dados.get("pedido_id"),
        "codigo_rastreio": dados.get("codigo_rastreio"),
        "tipo": tipo,
        "mensagem": mensagem,
        "criada_em": evento["ocorrido_em"],
    })


@app.post("/admin/reset", status_code=204)
def reset():
    notificacoes.clear()
    eventos_processados.clear()
    estado["contador"] = 0
    return Response(status_code=204)


@app.post("/eventos")
async def receber_evento(request: Request):
    evento = await ler_evento(request)
    chave = str(evento["id"])
    if chave in eventos_processados:
        return {"processado": False}
    tipo = evento["tipo"]
    dados = evento["dados"]
    pedido_id = dados.get("pedido_id")

    if tipo == "envio.criado":
        mensagem = "Pedido {}: envio criado. Rastreio: {}.".format(pedido_id, dados.get("codigo_rastreio"))
        registrar_notificacao(evento, "CRIADO", mensagem)
        eventos_processados.add(chave)
        return {"processado": True}

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if status in MENSAGENS_STATUS:
            mensagem = MENSAGENS_STATUS[status].format(pedido_id=pedido_id)
            registrar_notificacao(evento, status, mensagem)
        eventos_processados.add(chave)
        return {"processado": True}

    return {"processado": False}


@app.get("/notificacoes")
def listar_notificacoes(email: str = Query(...)):
    resultado = [n for n in notificacoes if n["email"] == email]
    resultado.sort(key=lambda n: n["id"])
    return resultado
