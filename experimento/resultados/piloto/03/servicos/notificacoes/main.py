# arquivo: servicos/notificacoes/main.py
import json

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_estado = {"notificacoes": [], "contador": 0, "processados": set()}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _h_erro(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _h_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _h_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(msg="Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", msg)


async def _ler_corpo(request):
    try:
        raw = await request.body()
        corpo = json.loads(raw)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo deve ser um objeto JSON.")
    return corpo


def _registrar(tipo, dados, ocorrido_em):
    numero = _estado["contador"] + 1
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    notificacao = {
        "id": f"NOT-{numero:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    }
    _estado["contador"] = numero
    _estado["notificacoes"].append(notificacao)


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["contador"] = 0
    _estado["processados"] = set()
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            _invalido(f"Campo {campo} ausente.")
    eid = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]
    if not isinstance(eid, str) or not isinstance(dados, dict):
        _invalido("Envelope inválido.")

    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        _registrar("CRIADO", dados, ocorrido_em)
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, ocorrido_em)
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        _invalido("Parâmetro email obrigatório.")
    itens = [n for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=itens)
