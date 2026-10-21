# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"^[A-Z0-9_]{2,20}$")

_transportadoras = {}


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


def _int(corpo, campo, minimo=None, maximo=None):
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if isinstance(valor, bool):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if not isinstance(valor, int):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if minimo is not None and valor < minimo:
        raise _invalido(f"Campo '{campo}' abaixo do mínimo.")
    if maximo is not None and valor > maximo:
        raise _invalido(f"Campo '{campo}' acima do máximo.")
    return valor


def _parse_bool_query(valor):
    if valor is None:
        return False
    v = valor.strip().lower()
    if v in ("true", "1", "yes", "on", "t", "y"):
        return True
    if v in ("false", "0", "no", "off", "f", "n"):
        return False
    raise _invalido("Parâmetro 'somente_ativas' deve ser booleano.")


@app.post("/admin/reset")
async def reset():
    _transportadoras.clear()
    return Response(status_code=204)


@app.post("/transportadoras")
async def cadastrar(request: Request):
    corpo = await _ler_corpo(request)

    if "id" not in corpo or not isinstance(corpo["id"], str) or not RE_ID.match(corpo["id"]):
        raise _invalido("Campo 'id' inválido.")
    tid = corpo["id"]

    nome = corpo.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' inválido.")

    ufs = corpo.get("ufs_atendidas")
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' inválido.")
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("UF inválida em 'ufs_atendidas'.")
    if len(set(ufs)) != len(ufs):
        raise _invalido("UF repetida em 'ufs_atendidas'.")

    tarifa_base = _int(corpo, "tarifa_base_centavos", minimo=0)
    tarifa_kg = _int(corpo, "tarifa_kg_centavos", minimo=0)
    adicional = _int(corpo, "adicional_interestadual_pct", minimo=0, maximo=100)
    prazo = _int(corpo, "prazo_dias_uteis", minimo=1)
    dias_adic = _int(corpo, "dias_adicionais_interestadual", minimo=0)
    peso_max = _int(corpo, "peso_maximo_g", minimo=1)

    if tid in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", "Já existe transportadora com este id.")

    transportadora = {
        "id": tid,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": tarifa_base,
        "tarifa_kg_centavos": tarifa_kg,
        "adicional_interestadual_pct": adicional,
        "prazo_dias_uteis": prazo,
        "dias_adicionais_interestadual": dias_adic,
        "peso_maximo_g": peso_max,
        "ativa": True,
    }
    _transportadoras[tid] = transportadora
    return JSONResponse(status_code=201, content=transportadora)


@app.get("/transportadoras")
async def listar(request: Request):
    somente_ativas = _parse_bool_query(request.query_params.get("somente_ativas"))
    lista = [
        _transportadoras[k]
        for k in sorted(_transportadoras)
        if not somente_ativas or _transportadoras[k]["ativa"]
    ]
    return JSONResponse(status_code=200, content=lista)


@app.get("/transportadoras/{tid}")
async def consultar(tid: str):
    if tid not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return JSONResponse(status_code=200, content=_transportadoras[tid])


@app.patch("/transportadoras/{tid}")
async def alterar_ativa(tid: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo or not isinstance(corpo["ativa"], bool):
        raise _invalido("Campo 'ativa' ausente ou não booleano.")
    if tid not in _transportadoras:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    _transportadoras[tid]["ativa"] = corpo["ativa"]
    return JSONResponse(status_code=200, content=_transportadoras[tid])
