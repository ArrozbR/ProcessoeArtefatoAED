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

_estado = {"notificacoes": [], "contador": 0, "eventos_processados": set()}


class ErroApi(Exception):
    def __init__(self, status: int, erro: str, mensagem: str):
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status: int, erro: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroApi)
async def _tratar_erro_api(request: Request, exc: ErroApi):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request: Request, exc: RequestValidationError):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem: str = "Dados inválidos na requisição.") -> ErroApi:
    return ErroApi(422, "DADOS_INVALIDOS", mensagem)


async def _ler_json(request: Request) -> dict:
    corpo = await request.body()
    try:
        dados = json.loads(corpo)
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(dados, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return dados


def _chave_evento(eid) -> str:
    return json.dumps(eid, sort_keys=True)


def _registrar(tipo: str, dados: dict, criada_em) -> None:
    numero = _estado["contador"] + 1
    pedido_id = dados["pedido_id"]
    codigo = dados["codigo_rastreio"]
    notificacao = {
        "id": f"NOT-{numero:06d}",
        "email": dados["destinatario_email"],
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": criada_em,
    }
    _estado["contador"] = numero
    _estado["notificacoes"].append(notificacao)


@app.post("/eventos")
async def consumir_evento(request: Request):
    envelope = await _ler_json(request)
    for c in ("id", "tipo", "ocorrido_em", "dados"):
        if c not in envelope:
            raise _invalido(f"Campo obrigatório ausente no envelope: {c}.")
    dados = envelope["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo dados deve ser um objeto.")

    chave = _chave_evento(envelope["id"])
    if chave in _estado["eventos_processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = envelope["tipo"]
    if tipo == "envio.criado":
        for c in ("pedido_id", "codigo_rastreio", "destinatario_email"):
            if c not in dados:
                raise _invalido(f"Campo ausente em dados: {c}.")
        _registrar("CRIADO", dados, envelope["ocorrido_em"])
        _estado["eventos_processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        if "status" not in dados:
            raise _invalido("Campo ausente em dados: status.")
        status = dados["status"]
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            for c in ("pedido_id", "codigo_rastreio", "destinatario_email"):
                if c not in dados:
                    raise _invalido(f"Campo ausente em dados: {c}.")
            _registrar(status, dados, envelope["ocorrido_em"])
        _estado["eventos_processados"].add(chave)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
async def listar_notificacoes(request: Request):
    params = request.query_params
    if "email" not in params:
        raise _invalido("Parâmetro email é obrigatório.")
    email = params["email"]
    resultado = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=resultado)


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["contador"] = 0
    _estado["eventos_processados"] = set()
    return Response(status_code=204)
