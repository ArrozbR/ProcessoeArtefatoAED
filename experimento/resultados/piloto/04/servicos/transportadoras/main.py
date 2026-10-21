# arquivo: servicos/transportadoras/main.py
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

app = FastAPI()

UFS_VALIDAS = {"SP", "RJ", "ES", "MG", "BA", "PR", "SC", "RS"}
RE_ID = re.compile(r"[A-Z0-9_]{2,20}")

_transportadoras = {}


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


def _eh_int(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def _int_campo(corpo, campo, minimo=None, maximo=None):
    if campo not in corpo:
        raise _invalido(f"Campo '{campo}' ausente.")
    valor = corpo[campo]
    if not _eh_int(valor):
        raise _invalido(f"Campo '{campo}' deve ser inteiro.")
    if minimo is not None and valor < minimo:
        raise _invalido(f"Campo '{campo}' abaixo do mínimo permitido.")
    if maximo is not None and valor > maximo:
        raise _invalido(f"Campo '{campo}' acima do máximo permitido.")
    return valor


def _copia(t):
    return {
        "id": t["id"],
        "nome": t["nome"],
        "ufs_atendidas": list(t["ufs_atendidas"]),
        "tarifa_base_centavos": t["tarifa_base_centavos"],
        "tarifa_kg_centavos": t["tarifa_kg_centavos"],
        "adicional_interestadual_pct": t["adicional_interestadual_pct"],
        "prazo_dias_uteis": t["prazo_dias_uteis"],
        "dias_adicionais_interestadual": t["dias_adicionais_interestadual"],
        "peso_maximo_g": t["peso_maximo_g"],
        "ativa": t["ativa"],
    }


@app.post("/transportadoras", status_code=201)
async def cadastrar_transportadora(request: Request):
    corpo = await _ler_corpo(request)

    if "id" not in corpo:
        raise _invalido("Campo 'id' ausente.")
    id_ = corpo["id"]
    if not isinstance(id_, str) or not RE_ID.fullmatch(id_):
        raise _invalido("Campo 'id' deve ter de 2 a 20 caracteres entre A-Z, 0-9 e _.")

    if "nome" not in corpo:
        raise _invalido("Campo 'nome' ausente.")
    nome = corpo["nome"]
    if not isinstance(nome, str) or not nome.strip():
        raise _invalido("Campo 'nome' não pode ser vazio.")

    if "ufs_atendidas" not in corpo:
        raise _invalido("Campo 'ufs_atendidas' ausente.")
    ufs = corpo["ufs_atendidas"]
    if not isinstance(ufs, list) or len(ufs) == 0:
        raise _invalido("Campo 'ufs_atendidas' deve ser uma lista não vazia.")
    vistas = set()
    for uf in ufs:
        if not isinstance(uf, str) or uf not in UFS_VALIDAS:
            raise _invalido("Campo 'ufs_atendidas' contém UF inválida.")
        if uf in vistas:
            raise _invalido("Campo 'ufs_atendidas' contém UF repetida.")
        vistas.add(uf)

    tarifa_base = _int_campo(corpo, "tarifa_base_centavos", minimo=0)
    tarifa_kg = _int_campo(corpo, "tarifa_kg_centavos", minimo=0)
    adicional = _int_campo(corpo, "adicional_interestadual_pct", minimo=0, maximo=100)
    prazo = _int_campo(corpo, "prazo_dias_uteis", minimo=1)
    dias_adicionais = _int_campo(corpo, "dias_adicionais_interestadual", minimo=0)
    peso_maximo = _int_campo(corpo, "peso_maximo_g", minimo=1)

    if id_ in _transportadoras:
        raise ErroAPI(409, "TRANSPORTADORA_DUPLICADA", f"Já existe transportadora com id '{id_}'.")

    transportadora = {
        "id": id_,
        "nome": nome,
        "ufs_atendidas": list(ufs),
        "tarifa_base_centavos": tarifa_base,
        "tarifa_kg_centavos": tarifa_kg,
        "adicional_interestadual_pct": adicional,
        "prazo_dias_uteis": prazo,
        "dias_adicionais_interestadual": dias_adicionais,
        "peso_maximo_g": peso_maximo,
        "ativa": True,
    }
    _transportadoras[id_] = transportadora
    return _copia(transportadora)


@app.get("/transportadoras")
async def listar_transportadoras(somente_ativas: bool = False):
    resultado = []
    for id_ in sorted(_transportadoras):
        t = _transportadoras[id_]
        if somente_ativas and not t["ativa"]:
            continue
        resultado.append(_copia(t))
    return resultado


@app.get("/transportadoras/{id_transportadora}")
async def consultar_transportadora(id_transportadora: str):
    t = _transportadoras.get(id_transportadora)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    return _copia(t)


@app.patch("/transportadoras/{id_transportadora}")
async def alterar_ativa(id_transportadora: str, request: Request):
    corpo = await _ler_corpo(request)
    if "ativa" not in corpo:
        raise _invalido("Campo 'ativa' ausente.")
    ativa = corpo["ativa"]
    if not isinstance(ativa, bool):
        raise _invalido("Campo 'ativa' deve ser booleano.")
    t = _transportadoras.get(id_transportadora)
    if t is None:
        raise ErroAPI(404, "TRANSPORTADORA_NAO_ENCONTRADA", "Transportadora não encontrada.")
    t["ativa"] = ativa
    return _copia(t)


@app.post("/admin/reset", status_code=204)
async def reiniciar():
    _transportadoras.clear()
    return Response(status_code=204)
