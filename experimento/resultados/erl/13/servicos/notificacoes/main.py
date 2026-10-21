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

_estado = {"notificacoes": [], "eventos": set(), "contador": 0}


class ErroAPI(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _erro(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
    if exc.status_code == 405:
        return _erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request):
    try:
        corpo = await request.body()
        return json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")


def _exigir(dados: dict, campos) -> None:
    for c in campos:
        if c not in dados:
            raise _invalido(f"Campo 'dados.{c}' ausente.")


def _registrar(tipo_notif: str, dados: dict, ocorrido_em) -> None:
    _estado["contador"] += 1
    pedido_id = dados["pedido_id"]
    codigo = dados["codigo_rastreio"]
    _estado["notificacoes"].append({
        "id": f"NOT-{_estado['contador']:06d}",
        "email": dados["destinatario_email"],
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo_notif,
        "mensagem": MENSAGENS[tipo_notif].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def consumir_evento(request: Request):
    evento = await _ler_json(request)
    if not isinstance(evento, dict):
        raise _invalido("Envelope deve ser um objeto JSON.")
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")

    eid = evento["id"]
    tipo = evento["tipo"]
    dados = evento["dados"]
    ocorrido_em = evento["ocorrido_em"]

    try:
        if eid in _estado["eventos"]:
            return JSONResponse(status_code=200, content={"processado": False})
    except TypeError:
        raise _invalido("Campo 'id' inválido.")

    if tipo == "envio.criado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("pedido_id", "codigo_rastreio", "destinatario_email"))
        _registrar("CRIADO", dados, ocorrido_em)
        _estado["eventos"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        if not isinstance(dados, dict):
            raise _invalido("Campo 'dados' inválido.")
        _exigir(dados, ("status",))
        status = dados["status"]
        if status in MENSAGENS and status != "CRIADO":
            _exigir(dados, ("pedido_id", "codigo_rastreio", "destinatario_email"))
            _registrar(status, dados, ocorrido_em)
        _estado["eventos"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    itens = [n for n in _estado["notificacoes"] if n["email"] == email]
    itens.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=copy.deepcopy(itens))


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["notificacoes"] = []
    _estado["eventos"] = set()
    _estado["contador"] = 0
    return Response(status_code=204)
