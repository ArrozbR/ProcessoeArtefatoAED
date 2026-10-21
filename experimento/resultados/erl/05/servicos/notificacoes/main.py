# arquivo: servicos/notificacoes/main.py
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

TIPOS_CONHECIDOS = {"envio.criado", "envio.status_atualizado"}
MENSAGENS = {
    "CRIADO": "Pedido {pedido_id}: envio criado. Rastreio: {codigo_rastreio}.",
    "SAIU_PARA_ENTREGA": "Pedido {pedido_id}: saiu para entrega.",
    "ENTREGUE": "Pedido {pedido_id}: entregue.",
    "FALHA_ENTREGA": "Pedido {pedido_id}: tentativa de entrega sem sucesso.",
    "DEVOLVIDO": "Pedido {pedido_id}: devolvido ao remetente.",
    "CANCELADO": "Pedido {pedido_id}: envio cancelado.",
}

_estado = {"contador": 0, "processados": set(), "notificacoes": []}


class ErroAPI(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str):
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status: int, codigo: str, mensagem: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request: Request, exc: ErroAPI):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


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


def _invalido(mensagem: str) -> ErroAPI:
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request: Request) -> dict:
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _chave_evento(valor) -> str:
    if isinstance(valor, str):
        return valor
    return repr(valor)


@app.post("/eventos")
async def consumir_evento(request: Request):
    evento = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in evento:
            raise _invalido(f"Envelope sem o campo '{campo}'.")

    chave = _chave_evento(evento["id"])
    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = evento["tipo"]
    if tipo not in TIPOS_CONHECIDOS:
        return JSONResponse(status_code=200, content={"processado": False})

    dados = evento["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")

    if tipo == "envio.criado":
        status = "CRIADO"
    else:
        if "status" not in dados:
            raise _invalido("Dados do evento sem 'status'.")
        status = dados["status"]

    notificacao = None
    if isinstance(status, str) and status in MENSAGENS:
        try:
            pedido_id = dados["pedido_id"]
            codigo_rastreio = dados["codigo_rastreio"]
            email = dados["destinatario_email"]
        except KeyError:
            raise _invalido("Dados do evento incompletos.")
        numero = _estado["contador"] + 1
        notificacao = {
            "id": f"NOT-{numero:06d}",
            "email": email,
            "pedido_id": pedido_id,
            "codigo_rastreio": codigo_rastreio,
            "tipo": status,
            "mensagem": MENSAGENS[status].format(pedido_id=pedido_id, codigo_rastreio=codigo_rastreio),
            "criada_em": evento["ocorrido_em"],
        }
        _estado["contador"] = numero
        _estado["notificacoes"].append(notificacao)

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar_notificacoes(request: Request):
    if "email" not in request.query_params:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    email = request.query_params["email"]
    resultado = [dict(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=resultado)


@app.post("/admin/reset", status_code=204)
async def reset():
    _estado["contador"] = 0
    _estado["processados"] = set()
    _estado["notificacoes"] = []
    return Response(status_code=204)
