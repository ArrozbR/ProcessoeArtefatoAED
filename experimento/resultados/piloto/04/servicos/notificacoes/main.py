# arquivo: servicos/notificacoes/main.py
import copy

from fastapi import FastAPI, Query, Request
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

_estado = {"processados": set(), "contador": 0, "notificacoes": []}


class ErroAPI(Exception):
    def __init__(self, status, erro, mensagem):
        super().__init__(mensagem)
        self.status = status
        self.erro = erro
        self.mensagem = mensagem


def _resposta_erro(status, erro, mensagem):
    return JSONResponse(status_code=status, content={"erro": erro, "mensagem": mensagem})


@app.exception_handler(ErroAPI)
async def _tratar_erro_api(request, exc):
    return _resposta_erro(exc.status, exc.erro, exc.mensagem)


@app.exception_handler(RequestValidationError)
async def _tratar_validacao(request, exc):
    return _resposta_erro(422, "DADOS_INVALIDOS", "Dados inválidos na requisição.")


@app.exception_handler(StarletteHTTPException)
async def _tratar_http(request, exc):
    if exc.status_code == 404:
        erro = "NAO_ENCONTRADO"
    elif exc.status_code == 405:
        erro = "METODO_NAO_PERMITIDO"
    else:
        erro = "ERRO"
    return _resposta_erro(exc.status_code, erro, str(exc.detail))


def _invalido(mensagem):
    return ErroAPI(422, "DADOS_INVALIDOS", mensagem)


async def _ler_corpo(request):
    try:
        corpo = await request.json()
    except Exception:
        raise _invalido("Corpo da requisição ausente ou não é JSON válido.")
    if not isinstance(corpo, dict):
        raise _invalido("O corpo da requisição deve ser um objeto JSON.")
    return corpo


def _chave_id(valor):
    if isinstance(valor, (dict, list)):
        return repr(valor)
    return str(valor)


def _campos(dados, nomes):
    if not isinstance(dados, dict):
        raise _invalido("Campo 'dados' deve ser um objeto.")
    for nome in nomes:
        if nome not in dados:
            raise _invalido(f"Campo 'dados.{nome}' ausente.")


@app.post("/eventos")
async def receber_evento(request: Request):
    corpo = await _ler_corpo(request)
    for campo in ("id", "tipo", "ocorrido_em", "dados"):
        if campo not in corpo:
            raise _invalido(f"Campo '{campo}' ausente no envelope.")

    chave = _chave_id(corpo["id"])
    if chave in _estado["processados"]:
        return {"processado": False}

    tipo_evento = corpo["tipo"]
    if not isinstance(tipo_evento, str) or tipo_evento not in TIPOS_CONHECIDOS:
        return {"processado": False}

    dados = corpo["dados"]
    if tipo_evento == "envio.criado":
        _campos(dados, ("pedido_id", "codigo_rastreio", "destinatario_email"))
        status = "CRIADO"
    else:
        _campos(dados, ("pedido_id", "codigo_rastreio", "destinatario_email", "status"))
        status = dados["status"]

    if isinstance(status, str) and status in MENSAGENS:
        numero = _estado["contador"] + 1
        notificacao = {
            "id": f"NOT-{numero:06d}",
            "email": dados["destinatario_email"],
            "pedido_id": dados["pedido_id"],
            "codigo_rastreio": dados["codigo_rastreio"],
            "tipo": status,
            "mensagem": MENSAGENS[status].format(
                pedido_id=dados["pedido_id"], codigo_rastreio=dados["codigo_rastreio"]
            ),
            "criada_em": corpo["ocorrido_em"],
        }
        _estado["contador"] = numero
        _estado["notificacoes"].append(notificacao)

    _estado["processados"].add(chave)
    return {"processado": True}


@app.get("/notificacoes")
async def listar_notificacoes(email: str = Query(...)):
    return [copy.deepcopy(n) for n in _estado["notificacoes"] if n["email"] == email]


@app.post("/admin/reset", status_code=204)
async def reiniciar():
    _estado["processados"] = set()
    _estado["contador"] = 0
    _estado["notificacoes"] = []
    return Response(status_code=204)
