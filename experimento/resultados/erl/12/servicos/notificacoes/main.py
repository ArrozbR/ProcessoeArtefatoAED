# arquivo: servicos/notificacoes/main.py
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

_estado = {"processados": set(), "notificacoes": [], "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO_HTTP", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição."):
    raise ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_envelope(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        _invalido("Corpo da requisição deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            _invalido(f"Envelope sem o campo '{campo}'.")
    if not isinstance(corpo["dados"], dict):
        _invalido("Campo 'dados' deve ser um objeto.")
    return corpo


def _registrar(tipo_notif: str, dados: dict, ocorrido_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    _estado["notificacoes"].append({
        "id": f"NOT-{_estado['contador']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo_notif,
        "mensagem": MENSAGENS[tipo_notif].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def consumir_evento(request: Request):
    env = await _ler_envelope(request)
    eid = str(env["id"])
    tipo = env["tipo"]
    dados = env["dados"]

    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        _registrar("CRIADO", dados, env["ocorrido_em"])
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, env["ocorrido_em"])
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(eid)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar_notificacoes(request: Request):
    if "email" not in request.query_params:
        _invalido("Parâmetro 'email' é obrigatório.")
    email = request.query_params.get("email")
    lista = [dict(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=lista)


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["processados"] = set()
    _estado["notificacoes"] = []
    _estado["contador"] = 0
    return Response(status_code=204)
