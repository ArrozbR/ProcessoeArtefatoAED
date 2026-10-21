# arquivo: servicos/notificacoes/main.py
import copy
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

_estado = {"notificacoes": [], "processados": set(), "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        bruto = await request.body()
        corpo = json.loads(bruto)
    except Exception:
        _invalido("Corpo JSON inválido.")
    if not isinstance(corpo, dict):
        _invalido("O corpo deve ser um objeto JSON.")
    return corpo


def _registrar(tipo_notificacao, dados, ocorrido_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": "NOT-%06d" % _estado["contador"],
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo_notificacao,
        "mensagem": MENSAGENS[tipo_notificacao].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/admin/reset")
def reset():
    _estado["notificacoes"].clear()
    _estado["processados"].clear()
    _estado["contador"] = 0
    return Response(status_code=204)


@app.post("/eventos")
async def consumir(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            _invalido(f"Campo '{campo}' ausente no envelope.")
    evento_id = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    if not isinstance(evento_id, str) or not isinstance(tipo, str) or not isinstance(dados, dict):
        _invalido("Envelope inválido.")

    if evento_id in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        _registrar("CRIADO", dados, corpo["ocorrido_em"])
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, corpo["ocorrido_em"])
        _estado["processados"].add(evento_id)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        _invalido("Parâmetro 'email' ausente.")
    itens = [n for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=copy.deepcopy(itens))
