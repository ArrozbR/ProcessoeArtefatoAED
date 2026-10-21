# arquivo: servicos/notificacoes/main.py
import copy

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
    def __init__(self, status, codigo, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem


def _resposta_erro(status, codigo, mensagem):
    return JSONResponse(status_code=status, content={"erro": codigo, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.codigo, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 422:
        return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição não é um JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("Corpo da requisição deve ser um objeto JSON.")
    return corpo


def _registrar(tipo, dados, ocorrido_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    numero = _estado["contador"] + 1
    _estado["contador"] = numero
    _estado["notificacoes"].append({
        "id": f"NOT-{numero:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    })


@app.post("/eventos")
async def receber_evento(request: Request):
    envelope = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in envelope:
            raise _invalido(f"Envelope sem o campo '{campo}'.")
    dados = envelope["dados"]
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' inválido.")
    chave = str(envelope["id"])

    if chave in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    tipo = envelope["tipo"]
    ocorrido_em = envelope["ocorrido_em"]
    if tipo == "envio.criado":
        _registrar("CRIADO", dados, ocorrido_em)
    elif tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, ocorrido_em)
    else:
        return JSONResponse(status_code=200, content={"processado": False})

    _estado["processados"].add(chave)
    return JSONResponse(status_code=200, content={"processado": True})


@app.get("/notificacoes")
async def listar_notificacoes(request: Request):
    if "email" not in request.query_params:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    email = request.query_params["email"]
    itens = [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]
    return JSONResponse(status_code=200, content=itens)


@app.post("/admin/reset")
async def reset():
    _estado["notificacoes"] = []
    _estado["processados"] = set()
    _estado["contador"] = 0
    return Response(status_code=204)
