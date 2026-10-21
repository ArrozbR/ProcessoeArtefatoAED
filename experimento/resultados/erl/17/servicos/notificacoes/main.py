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

_estado = {"processados": set(), "contador": 0, "notificacoes": []}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        return _resposta_erro(404, "RECURSO_NAO_ENCONTRADO", "Rota não encontrada.")
    if exc.status_code == 405:
        return _resposta_erro(405, "METODO_NAO_PERMITIDO", "Método não permitido.")
    return _resposta_erro(exc.status_code, "ERRO", str(exc.detail))


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _validar_envelope(corpo):
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")
    if not isinstance(corpo["id"], str) or not isinstance(corpo["tipo"], str):
        raise _invalido("Campos 'id' e 'tipo' devem ser texto.")
    if not isinstance(corpo["dados"], dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")


def _registrar(tipo, dados, ocorrido_em):
    pedido_id = dados.get("pedido_id")
    codigo = dados.get("codigo_rastreio")
    _estado["contador"] += 1
    notificacao = {
        "id": f"NOT-{_estado['contador']:06d}",
        "email": dados.get("destinatario_email"),
        "pedido_id": pedido_id,
        "codigo_rastreio": codigo,
        "tipo": tipo,
        "mensagem": MENSAGENS[tipo].format(pedido_id=pedido_id, codigo_rastreio=codigo),
        "criada_em": ocorrido_em,
    }
    _estado["notificacoes"].append(notificacao)


@app.post("/admin/reset")
async def reset():
    _estado["processados"] = set()
    _estado["contador"] = 0
    _estado["notificacoes"] = []
    return Response(status_code=204)


@app.post("/eventos")
async def eventos(request: Request):
    corpo = await _ler_corpo(request)
    _validar_envelope(corpo)

    eid = corpo["id"]
    tipo = corpo["tipo"]
    dados = corpo["dados"]
    ocorrido_em = corpo["ocorrido_em"]

    if eid in _estado["processados"]:
        return JSONResponse(status_code=200, content={"processado": False})

    if tipo == "envio.criado":
        _registrar("CRIADO", dados, ocorrido_em)
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    if tipo == "envio.status_atualizado":
        status = dados.get("status")
        if isinstance(status, str) and status in MENSAGENS and status != "CRIADO":
            _registrar(status, dados, ocorrido_em)
        _estado["processados"].add(eid)
        return JSONResponse(status_code=200, content={"processado": True})

    return JSONResponse(status_code=200, content={"processado": False})


@app.get("/notificacoes")
async def listar(request: Request):
    email = request.query_params.get("email")
    if email is None:
        raise _invalido("Parâmetro 'email' é obrigatório.")
    lista = [n for n in _estado["notificacoes"] if n["email"] == email]
    lista.sort(key=lambda n: n["id"])
    return JSONResponse(status_code=200, content=lista)
