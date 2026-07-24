"""
Backlog - Anjun Brasil
=============================================
App para subir a base de backlog (mesmo modelo cru de 87 colunas do
monitoramento_da_pontualidade_de_pedido) e acompanhar o painel de
pacotes parados: dias sem movimentação e dias desde o recebimento no
ponto, sempre calculados em relação a HOJE (view backlog_atual no banco,
nunca fica desatualizado).

Diferença importante em relação ao app de Pontualidade: aqui o
casamento de colunas é por TEXTO do cabeçalho (não por posição fixa),
porque esse arquivo pode vir com colunas extras no meio (ex: "Dia de
recebimento", "Dias compilados" -- cálculos que o time já faz na mão no
Excel). Colunas duplicadas (ex: "Centro real de chegada" aparece 3x)
são casadas na ordem em que aparecem, igual ao modelo original.
"""

import json
import os
import re
from datetime import datetime

import pandas as pd
import psycopg2
import plotly.graph_objects as go
import streamlit as st
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------
# Identidade visual Anjun Brasil
# ---------------------------------------------------------------------
ANJUN_GREEN = "#009946"
ANJUN_GREEN_DARK = "#00753A"
ANJUN_RED = "#E80115"
COR_CRITICO = "#C2410C"
COR_EXTRAVIO = "#C00000"

# UFs da regional que a Anjun responde -- fixo, não vem do banco, porque
# qualquer outro estado que aparecer nos dados é fora do escopo da operação.
UFS_REGIONAL = ["AM", "AP", "PA", "RR"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "anjun_logo.png")
MAPPING_PATH = os.path.join(BASE_DIR, "config", "column_mapping_base.json")

st.set_page_config(
    page_title="Backlog | Anjun Brasil",
    page_icon=LOGO_PATH,
    layout="wide",
)

st.markdown(
    f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Inter:wght@400;500;600&display=swap');

    html, body, [class*="css"] {{ font-family: 'Inter', -apple-system, sans-serif; }}
    h1, h2, h3, h4, h5, h6 {{ font-family: 'Manrope', sans-serif !important; letter-spacing: -0.01em; }}

    .block-container {{ padding-top: 2rem; max-width: 1300px; }}

    div[data-testid="stMetric"] {{
        background-color: #F0F7F2;
        border: 1px solid #E0EDE4;
        border-radius: 12px;
        padding: 0.9rem 1.1rem;
        transition: box-shadow 0.15s ease;
    }}
    div[data-testid="stMetric"]:hover {{ box-shadow: 0 4px 14px rgba(0,153,70,0.12); }}
    div[data-testid="stMetricValue"] {{ color: {ANJUN_GREEN_DARK}; font-family: 'Manrope', sans-serif; }}
    div[data-testid="stMetricLabel"] {{ font-weight: 600; color: #3D5245 !important; }}

    .stTabs [data-baseweb="tab"] {{ font-weight: 700; font-size: 1rem; }}
    .stButton > button[kind="primary"] {{
        background-color: {ANJUN_GREEN}; border-color: {ANJUN_GREEN}; font-weight: 600;
    }}
    .stButton > button[kind="primary"]:hover {{
        background-color: {ANJUN_GREEN_DARK}; border-color: {ANJUN_GREEN_DARK};
    }}

    /* Cartões ao redor de cada gráfico */
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        border-radius: 14px !important;
        border-color: #E7EEE9 !important;
        transition: box-shadow 0.18s ease, transform 0.18s ease, border-color 0.18s ease;
        padding: 0.2rem;
    }}
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {{
        box-shadow: 0 10px 26px rgba(22,36,28,0.08);
        transform: translateY(-2px);
        border-color: #C9DECE !important;
    }}

    .chart-title {{
        font-family: 'Manrope', sans-serif; font-weight: 700; font-size: 0.85rem;
        letter-spacing: 0.02em; color: var(--text-color, #16241C); margin-bottom: 0.4rem;
        opacity: 0.85;
    }}
    .section-title {{
        font-family: 'Manrope', sans-serif; font-weight: 800; font-size: 1.25rem;
        color: {ANJUN_GREEN}; margin: 0.4rem 0 0.8rem 0;
        display: flex; align-items: center; gap: 0.5rem;
    }}

    /* Visão DSP: fundo verde-claro fixo (poster pra compartilhar), cartoes
       brancos por cima, independente do tema claro/escuro do usuario.
       .st-key-dsp_view_bg vem de st.container(key="dsp_view_bg") no Python. */
    .st-key-dsp_view_bg {{
        background: linear-gradient(180deg, #EAF5EC 0%, #F5FAF6 100%);
        border-radius: 16px;
        padding: 1.2rem 1.2rem 1.6rem 1.2rem;
    }}
    .st-key-dsp_view_bg div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: #FFFFFF;
        border-color: #DCEBE0 !important;
    }}
    .st-key-dsp_view_bg .chart-title {{ color: #16241C !important; opacity: 1; }}
    </style>
    """,
    unsafe_allow_html=True,
)

col_logo, col_title = st.columns([1, 6])
with col_logo:
    st.image(LOGO_PATH, width=140)
with col_title:
    st.markdown(
        """
        <div style="padding-top: 0.6rem;">
            <h1 style="color:#00753A; margin-bottom:0;">Backlog</h1>
            <p style="color:#555; margin-top:0.2rem;">Anjun Brasil &middot; pacotes parados: dias sem movimentação e desde o recebimento</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------
# Conexão com o banco (mesmo projeto Supabase dos outros apps)
# ---------------------------------------------------------------------
@st.cache_resource
def get_connection():
    cfg = st.secrets["supabase"]
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
        user=cfg["user"], password=cfg["password"], sslmode="require",
    )
    conn.autocommit = True
    return conn


def run_query(sql: str, params=None) -> pd.DataFrame:
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        conn.rollback()
        raise


@st.cache_data
def load_column_mapping():
    with open(MAPPING_PATH, encoding="utf-8") as f:
        return json.load(f)


mapping = load_column_mapping()


# ---------------------------------------------------------------------
# Casamento de colunas por TEXTO (não por posição fixa) -- tolera
# colunas extras no meio do arquivo e lida com cabeçalhos duplicados
# casando na ordem em que aparecem.
# ---------------------------------------------------------------------
def resolver_colunas(header_row, mapping):
    posicoes = {}
    for idx, h in enumerate(header_row):
        h = "" if h is None else str(h)
        posicoes.setdefault(h, []).append(idx)

    usados = {}
    resolvido = []  # (slug, indice_no_arquivo ou None)
    faltando = []
    for m in mapping:
        texto = m["original"]
        n = usados.get(texto, 0)
        lista = posicoes.get(texto, [])
        if n < len(lista):
            resolvido.append((m["slug"], lista[n]))
            usados[texto] = n + 1
        else:
            resolvido.append((m["slug"], None))
            faltando.append(texto)

    todos_esperados = {m["original"] for m in mapping}
    extras = [h for h in header_row if h and str(h) not in todos_esperados]
    return resolvido, faltando, extras


def to_text(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (pd.Timestamp, datetime)):
        return str(v)
    if isinstance(v, pd.Timedelta):
        return str(v)
    return str(v)


def parse_periodo_from_filename(filename):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", filename)
    if m:
        return m.group(1)
    m = re.search(r"(\d{1,2}_\d{1,2}_A_\d{1,2}_\d{1,2})", filename, re.IGNORECASE)
    return m.group(1) if m else filename.rsplit(".", 1)[0]


def insert_backlog(data_df, resolvido, arquivo_origem, periodo, batch_size=1000, progress_cb=None):
    """Upsert por numero_do_waybill -- reenviar um arquivo, ou um pedido
    que já apareceu numa carga anterior, nunca duplica: atualiza a linha.

    Alem disso, remove sozinho qualquer linha de HOJE que veio de um
    arquivo diferente do que está sendo subido agora -- isso evita que um
    upload de manhã e outro à tarde no mesmo dia se somem (o backlog da
    tarde deve SUBSTITUIR o da manhã, não empilhar em cima)."""
    slugs = [s for s, _ in resolvido]
    idxs = [i for _, i in resolvido]  # pode conter None se a coluna nao existir no arquivo
    cols_sql = ["arquivo_origem", "periodo_referencia", "row_num"] + slugs
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols_sql if c != "numero_do_waybill")
    set_clause += ", data_importacao = now(), data_snapshot = CURRENT_DATE"
    insert_sql = (
        "INSERT INTO public.backlog (" + ", ".join(cols_sql) + ") VALUES %s "
        "ON CONFLICT (numero_do_waybill) DO UPDATE SET " + set_clause
    )

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.backlog")
        antes = cur.fetchone()[0]

    total = 0
    n = len(data_df)
    with conn.cursor() as cur:
        for start in range(0, n, batch_size):
            chunk = data_df.iloc[start:start + batch_size]
            rows = []
            for i, row in chunk.iterrows():
                vals = [arquivo_origem, periodo, i + 1] + [
                    (to_text(row[idx]) if idx is not None else None) for idx in idxs
                ]
                rows.append(tuple(vals))
            execute_values(cur, insert_sql, rows)
            total += len(rows)
            if progress_cb:
                progress_cb(total / n)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.backlog")
        apos_upsert = cur.fetchone()[0]

    # Limpeza: remove sobras de um upload ANTERIOR de hoje, de outro arquivo
    removidos = 0
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.backlog WHERE data_snapshot = CURRENT_DATE AND arquivo_origem != %s",
            (arquivo_origem,),
        )
        removidos = cur.rowcount

    novos = apos_upsert - antes
    atualizados = total - novos
    return total, novos, atualizados, removidos


def registrar_snapshot_historico():
    """Grava (ou atualiza) o resumo agregado do dia na backlog_historico_diario.
    Chamada automaticamente ao fim de cada upload -- é o que dá origem ao
    gráfico de tendência (backlog crescendo ou caindo ao longo do tempo)."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO public.backlog_historico_diario
                (data_snapshot, total, d0_4, d5_13, critico, extravio,
                 media_dias_recebimento, media_dias_sem_movimentacao)
            SELECT
                data_snapshot,
                count(*),
                count(*) FILTER (WHERE faixa_recebimento = '0 a 4 dias'),
                count(*) FILTER (WHERE faixa_recebimento = '05 a 13 dias'),
                count(*) FILTER (WHERE faixa_recebimento = '14 a 20 dias (Crítico)'),
                count(*) FILTER (WHERE faixa_recebimento = 'Mais de 20 (Extravio)'),
                avg(dias_desde_recebimento),
                avg(dias_sem_movimentacao)
            FROM public.backlog_atual
            WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)
            GROUP BY data_snapshot
            ON CONFLICT (data_snapshot) DO UPDATE SET
                total = EXCLUDED.total, d0_4 = EXCLUDED.d0_4, d5_13 = EXCLUDED.d5_13,
                critico = EXCLUDED.critico, extravio = EXCLUDED.extravio,
                media_dias_recebimento = EXCLUDED.media_dias_recebimento,
                media_dias_sem_movimentacao = EXCLUDED.media_dias_sem_movimentacao
        """, {"uf_regional": UFS_REGIONAL})


tab_upload, tab_painel, tab_dsp = st.tabs(["Upload da Base", "Painel de Backlog", "Visão DSP"])

# ===================== ABA UPLOAD =====================
def render_upload():
    st.subheader("Subir arquivo de backlog")
    st.caption(
        "Aceita o export padrão (mesmo modelo do monitoramento_da_pontualidade_de_pedido). "
        "Colunas extras que o arquivo já trouxer (ex: cálculos manuais no Excel) são "
        "ignoradas automaticamente -- os dias em atraso são recalculados sempre com a data de hoje."
    )
    st.caption(
        "🔒 Protegido contra duplicidade: se um pedido já existir na base, a linha é "
        "atualizada, não duplicada. Pode subir tranquilo mesmo sem ter certeza se aquele "
        "arquivo já foi enviado antes."
    )

    uploaded = st.file_uploader("Arquivo .xlsx", type=["xlsx"])

    if uploaded is not None:
        try:
            raw_df = pd.read_excel(uploaded, sheet_name=0, header=None)
        except Exception as e:
            st.error(f"Não consegui ler o arquivo: {e}")
            return

        header_row = raw_df.iloc[0].tolist()
        data_df = raw_df.iloc[1:].reset_index(drop=True)

        resolvido, faltando, extras = resolver_colunas(header_row, mapping)

        if faltando:
            st.error(
                "Esse arquivo está sem colunas que a tabela `backlog` espera. "
                "Antes de subir, confirma se é o modelo certo (fale com quem mantém o banco)."
            )
            st.write(f"**Faltando ({len(faltando)}):**")
            st.code("\n".join(faltando))
        else:
            st.success(f"Modelo confere: {len(data_df)} linhas, {len(header_row)} colunas no arquivo.")
            if extras:
                with st.expander(f"ℹ️ {len(extras)} coluna(s) extra(s) no arquivo (ignoradas)"):
                    st.caption(
                        "Essas colunas existem no arquivo mas não fazem parte do modelo -- "
                        "provavelmente cálculos que vocês já fazem no Excel. Não são salvas; "
                        "os indicadores equivalentes são recalculados pela view do banco."
                    )
                    st.code("\n".join(extras))

            preview_cols = {slug: (data_df[idx] if idx is not None else pd.Series([None] * len(data_df)))
                             for slug, idx in resolvido[:10]}
            st.dataframe(pd.DataFrame(preview_cols).head(10), use_container_width=True)

            col_a, col_b = st.columns(2)
            with col_a:
                periodo = st.text_input(
                    "Período de referência (data da extração)",
                    value=parse_periodo_from_filename(uploaded.name),
                )
            with col_b:
                st.text_input("Arquivo de origem", value=uploaded.name, disabled=True)

            if st.button("Confirmar e subir para o banco", type="primary"):
                progress = st.progress(0.0, text="Enviando...")

                def _cb(frac):
                    progress.progress(min(frac, 1.0), text=f"Enviando... {frac:.0%}")

                n, novos, atualizados, removidos = insert_backlog(data_df, resolvido, uploaded.name, periodo, progress_cb=_cb)
                registrar_snapshot_historico()
                progress.empty()
                msg = (
                    f"{n} linhas processadas: **{novos} pedidos novos**, "
                    f"**{atualizados} já existentes** atualizados (nenhum duplicado)."
                )
                if removidos:
                    msg += (
                        f" **{removidos} linha(s) de um upload anterior de hoje (outro arquivo) foram removidas** "
                        f"-- o backlog de hoje passa a refletir só esse arquivo."
                    )
                msg += " Snapshot de hoje registrado no histórico."
                st.success(msg)
                st.balloons()
                st.cache_data.clear()

# ===================== ABA PAINEL =====================
with tab_upload:
    render_upload()

with tab_painel:
    st.subheader("Painel de Backlog")

    # -------------------- Aviso de atualização --------------------
    snap_info = run_query("SELECT max(data_snapshot) AS ultimo FROM public.backlog")
    ultimo_snapshot = snap_info.iloc[0]["ultimo"] if not snap_info.empty else None
    hoje = datetime.now().date()

    if ultimo_snapshot is None:
        st.info("Base ainda vazia -- sobe um arquivo na aba Upload.")
    elif ultimo_snapshot == hoje:
        st.success(f"🟢 Backlog de **hoje** ({hoje:%d/%m/%Y}) — 今天的积压数据。O painel abaixo mostra só a carga mais recente.")
    else:
        dias_atras = (hoje - ultimo_snapshot).days
        st.warning(
            f"⚠️ Os dados são de **{ultimo_snapshot:%d/%m/%Y}** ({dias_atras} dia(s) atrás), não de hoje. "
            f"数据不是今天的，是 {dias_atras} 天前的。Sobe um arquivo novo na aba Upload pra atualizar."
        )

    if ultimo_snapshot is None:
        st.stop()

    # -------------------- Filtros --------------------
    opcoes_uf = UFS_REGIONAL
    opcoes_ponto = run_query("SELECT DISTINCT ponto_de_entrada AS v FROM public.backlog_atual WHERE ponto_de_entrada IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_supervisor = run_query("SELECT DISTINCT supervisor AS v FROM public.backlog_atual WHERE supervisor IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_status = run_query("SELECT DISTINCT status_do_pacote AS v FROM public.backlog_atual WHERE status_do_pacote IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_merchant = run_query("SELECT DISTINCT nome_do_cliente_merchant AS v FROM public.backlog_atual WHERE nome_do_cliente_merchant IS NOT NULL ORDER BY 1")["v"].tolist()
    opcoes_cliente = run_query("SELECT DISTINCT nome_do_cliente AS v FROM public.backlog_atual WHERE nome_do_cliente IS NOT NULL ORDER BY 1")["v"].tolist()

    with st.expander("Filtros / 筛选", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            f_uf = st.multiselect("UF (do ponto de entrada)", opcoes_uf)
        with c2:
            f_ponto = st.multiselect("Ponto de Entrada / DSP", opcoes_ponto)
        with c3:
            f_supervisor = st.multiselect("Supervisor", opcoes_supervisor)

        c4, c5, c6 = st.columns(3)
        with c4:
            f_status = st.multiselect("Status do Pacote", opcoes_status)
        with c5:
            f_merchant = st.multiselect("Cliente Merchant / 商户名称", opcoes_merchant)
        with c6:
            f_cliente = st.multiselect("Nome do Cliente / 客户名称", opcoes_cliente)

    clauses, params = ["estado_do_ponto_de_entrada = ANY(%(uf_regional)s)"], {"uf_regional": UFS_REGIONAL}
    if f_uf:
        clauses.append("estado_do_ponto_de_entrada = ANY(%(uf)s)")
        params["uf"] = f_uf
    if f_ponto:
        clauses.append("ponto_de_entrada = ANY(%(ponto)s)")
        params["ponto"] = f_ponto
    if f_supervisor:
        clauses.append("supervisor = ANY(%(supervisor)s)")
        params["supervisor"] = f_supervisor
    if f_status:
        clauses.append("status_do_pacote = ANY(%(status)s)")
        params["status"] = f_status
    if f_merchant:
        clauses.append("nome_do_cliente_merchant = ANY(%(merchant)s)")
        params["merchant"] = f_merchant
    if f_cliente:
        clauses.append("nome_do_cliente = ANY(%(cliente)s)")
        params["cliente"] = f_cliente
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    and_or_where = " AND " if where_sql else "WHERE "

    total = run_query(f"SELECT count(*) AS n FROM public.backlog_atual {where_sql}", params).iloc[0]["n"]

    if total == 0:
        st.warning("Nenhum pacote encontrado com esse filtro.")
    else:
        cor_faixa = {
            "0 a 4 dias": ANJUN_GREEN, "05 a 13 dias": "#D4A017",
            "14 a 20 dias (Crítico)": COR_CRITICO, "Mais de 20 (Extravio)": COR_EXTRAVIO,
        }
        ordem_faixa = list(cor_faixa.keys())

        kpi = run_query(
            f"""SELECT count(*) AS total, avg(dias_sem_movimentacao) AS media_mov,
                       avg(dias_desde_recebimento) AS media_receb,
                       count(*) FILTER (WHERE faixa_recebimento IN ('14 a 20 dias (Crítico)','Mais de 20 (Extravio)')) AS n_atencao
                FROM public.backlog_atual {where_sql}""",
            params,
        ).iloc[0]

        # -------------------- KPIs --------------------
        st.markdown('<div class="section-title">📊 Visão Geral / 总览</div>', unsafe_allow_html=True)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pacotes em backlog", f"{int(kpi['total']):,}".replace(",", "."))
        k2.metric("Média dias sem movimentação", f"{kpi['media_mov']:.1f}")
        k3.metric("Média dias desde recebimento", f"{kpi['media_receb']:.1f}")
        pct_atencao = 100 * kpi["n_atencao"] / kpi["total"] if kpi["total"] else 0
        k4.metric("Crítico + Extravio", f"{int(kpi['n_atencao']):,}".replace(",", "."), delta=f"{pct_atencao:.1f}% do total", delta_color="inverse")

        st.divider()

        # -------------------- Tendência --------------------
        st.markdown('<div class="section-title">📈 Tendência do Backlog / 积压趋势</div>', unsafe_allow_html=True)
        with st.container(border=True):
            st.caption("Um ponto por dia de upload. Com só uma carga ainda, vira linha de verdade a partir do próximo envio.")
            hist = run_query("SELECT * FROM public.backlog_historico_diario ORDER BY data_snapshot")
            if len(hist) < 2:
                st.info(f"Só há 1 snapshot registrado até agora ({hist.iloc[0]['data_snapshot']:%d/%m}, total {int(hist.iloc[0]['total']):,}). "
                        "A tendência aparece a partir do 2º dia de upload.".replace(",", "."))
            else:
                fig = go.Figure()
                for faixa, col, cor in [("0 a 4 dias", "d0_4", ANJUN_GREEN), ("05 a 13 dias", "d5_13", "#D4A017"),
                                         ("14 a 20 dias (Crítico)", "critico", COR_CRITICO), ("Mais de 20 (Extravio)", "extravio", COR_EXTRAVIO)]:
                    fig.add_trace(go.Scatter(x=hist["data_snapshot"], y=hist[col], name=faixa, mode="lines+markers",
                                              stackgroup="one", line=dict(color=cor)))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=320, margin=dict(l=10, r=10, t=10, b=10),
                                   legend=dict(orientation="h", yanchor="bottom", y=1.02), yaxis_title="Pacotes")
                st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # -------------------- Faixa x Responsável --------------------
        st.markdown('<div class="section-title">👤 Backlog por Responsável, detalhado por faixa / 按负责人及天数分布</div>', unsafe_allow_html=True)
        df_sf = run_query(
            f"""SELECT COALESCE(supervisor, '(sem responsável)') AS supervisor, faixa_recebimento, count(*) AS n
                FROM public.backlog_atual {where_sql} {and_or_where} faixa_recebimento IS NOT NULL
                GROUP BY supervisor, faixa_recebimento""",
            params,
        )
        with st.container(border=True):
            if not df_sf.empty:
                ordem_sup = df_sf.groupby("supervisor")["n"].sum().sort_values(ascending=False).index.tolist()
                fig = go.Figure()
                for faixa in ordem_faixa:
                    sub = df_sf[df_sf["faixa_recebimento"] == faixa].set_index("supervisor").reindex(ordem_sup).fillna(0)
                    fig.add_trace(go.Bar(name=faixa, x=ordem_sup, y=sub["n"], marker_color=cor_faixa[faixa]))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
barmode="stack", height=360, margin=dict(l=10, r=10, t=10, b=10),
                                   legend=dict(orientation="h", yanchor="bottom", y=1.02))
                st.plotly_chart(fig, use_container_width=True)
        st.caption("Colunas: `supervisor` (via `ponto_de_entrada`) × `faixa_recebimento`. Mostra se o volume de um responsável é saudável (verde) ou concentrado em crítico/extravio (laranja escuro/vermelho).")

        st.divider()

        # -------------------- Distribuições --------------------
        st.markdown('<div class="section-title">📐 Distribuições / 分布</div>', unsafe_allow_html=True)
        colA, colB = st.columns(2)
        with colA, st.container(border=True):
            st.markdown('<div class="chart-title">DIAS DESDE O RECEBIMENTO</div>', unsafe_allow_html=True)
            df_faixa = run_query(
                f"""SELECT faixa_recebimento, count(*) AS n FROM public.backlog_atual {where_sql}
                    {and_or_where} faixa_recebimento IS NOT NULL GROUP BY faixa_recebimento""",
                params,
            )
            df_faixa["ordem"] = df_faixa["faixa_recebimento"].apply(lambda x: ordem_faixa.index(x) if x in ordem_faixa else 99)
            df_faixa = df_faixa.sort_values("ordem")
            fig = go.Figure(go.Pie(
                labels=df_faixa["faixa_recebimento"], values=df_faixa["n"], hole=0.55,
                marker_colors=[cor_faixa.get(f, "#999") for f in df_faixa["faixa_recebimento"]],
                textinfo="value+percent", texttemplate="%{value:,}<br>(%{percent})",
                textposition="auto", insidetextorientation="horizontal",
            ))
            fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=340, margin=dict(l=30, r=30, t=10, b=10),
                               legend=dict(orientation="h", yanchor="bottom", y=-0.2, font=dict(size=10)))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Coluna: `faixa_recebimento` (`tempo_de_inbound_no_ponto` vs hoje)")

        with colB, st.container(border=True):
            st.markdown('<div class="chart-title">DIAS SEM MOVIMENTAÇÃO</div>', unsafe_allow_html=True)
            st.caption("Diferente da faixa ao lado: aqui é *quando foi o último rastreio*, não *quando chegou*. Um pacote pode ter chegado há pouco mas já estar sem mexer.")
            df_mov = run_query(
                f"""SELECT
                      CASE WHEN dias_sem_movimentacao <= 2 THEN '0 a 2 dias'
                           WHEN dias_sem_movimentacao <= 5 THEN '3 a 5 dias'
                           WHEN dias_sem_movimentacao <= 10 THEN '6 a 10 dias'
                           ELSE 'Mais de 10 dias' END AS faixa_mov,
                      count(*) AS n
                    FROM public.backlog_atual {where_sql}
                    {and_or_where} dias_sem_movimentacao IS NOT NULL GROUP BY faixa_mov""",
                params,
            )
            ordem_mov = ["0 a 2 dias", "3 a 5 dias", "6 a 10 dias", "Mais de 10 dias"]
            cores_mov = [ANJUN_GREEN, "#D4A017", COR_CRITICO, COR_EXTRAVIO]
            df_mov["ordem"] = df_mov["faixa_mov"].apply(lambda x: ordem_mov.index(x) if x in ordem_mov else 99)
            df_mov = df_mov.sort_values("ordem")
            fig = go.Figure(go.Bar(
                x=df_mov["faixa_mov"], y=df_mov["n"],
                marker_color=[cores_mov[ordem_mov.index(f)] for f in df_mov["faixa_mov"]],
                text=[f"{v:,}".replace(",", ".") for v in df_mov["n"]], textposition="outside", cliponaxis=False,
            ))
            fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Pacotes")
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Coluna: `dias_sem_movimentacao` (`ultimo_data_de_rastreio` vs hoje)")

        st.divider()

        # -------------------- Geografia e rede --------------------
        st.markdown('<div class="section-title">🗺️ Geografia e Rede / 地理与网点</div>', unsafe_allow_html=True)
        colC, colD = st.columns(2)
        with colC, st.container(border=True):
            st.markdown('<div class="chart-title">POR ESTADO</div>', unsafe_allow_html=True)
            df_uf = run_query(
                f"""SELECT estado_do_ponto_de_entrada AS grupo, count(*) AS n FROM public.backlog_atual {where_sql}
                    {and_or_where} estado_do_ponto_de_entrada IS NOT NULL GROUP BY grupo ORDER BY n DESC""",
                params,
            )
            fig = go.Figure(go.Bar(x=df_uf["grupo"], y=df_uf["n"], marker_color=ANJUN_GREEN_DARK,
                                    text=[f"{v:,}".replace(",", ".") for v in df_uf["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=340, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Coluna: `estado_do_ponto_de_entrada`")

        with colD, st.container(border=True):
            st.markdown('<div class="chart-title">POR PONTO DE ENTRADA / DSP (TOP 15)</div>', unsafe_allow_html=True)
            df_dsp = run_query(
                f"""SELECT ponto_de_entrada AS grupo, count(*) AS n FROM public.backlog_atual {where_sql}
                    {and_or_where} ponto_de_entrada IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 15""",
                params,
            )
            fig = go.Figure(go.Bar(x=df_dsp["n"], y=df_dsp["grupo"], orientation="h", marker_color=COR_CRITICO,
                                    text=[f"{v:,}".replace(",", ".") for v in df_dsp["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=340, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Coluna: `ponto_de_entrada` (não `ponto_de_entrega` -- a maioria do backlog ainda não tem ponto final)")

        st.divider()

        # -------------------- Cliente e motivo --------------------
        st.markdown('<div class="section-title">🏷️ Cliente e Motivo / 客户与原因</div>', unsafe_allow_html=True)
        colE, colF = st.columns(2)
        with colE, st.container(border=True):
            st.markdown('<div class="chart-title">POR CLIENTE MERCHANT (TOP 15)</div>', unsafe_allow_html=True)
            st.caption("No painel de referência isso estava rotulado \"Status do Pacote\" -- os valores eram nomes de empresa, corrigido aqui.")
            df_merchant = run_query(
                f"""SELECT nome_do_cliente_merchant AS grupo, count(*) AS n FROM public.backlog_atual {where_sql}
                    {and_or_where} nome_do_cliente_merchant IS NOT NULL GROUP BY grupo ORDER BY n DESC LIMIT 15""",
                params,
            )
            fig = go.Figure(go.Bar(x=df_merchant["n"], y=df_merchant["grupo"], orientation="h", marker_color=ANJUN_GREEN,
                                    text=[f"{v:,}".replace(",", ".") for v in df_merchant["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=380, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Coluna: `nome_do_cliente_merchant`")

        with colF, st.container(border=True):
            st.markdown('<div class="chart-title">MOTIVO DA OCORRÊNCIA — SÓ CRÍTICO + EXTRAVIO</div>', unsafe_allow_html=True)
            st.caption("Coluna nova nessa análise: hoje não era usada. Mostra só os pacotes em atenção (14+ dias), pra separar 'perda confirmada' (financeiro) de 'endereço errado' (operacional).")
            df_motivo = run_query(
                f"""SELECT motivo_da_ocorrencia AS grupo, count(*) AS n FROM public.backlog_atual {where_sql}
                    {and_or_where} faixa_recebimento IN ('14 a 20 dias (Crítico)','Mais de 20 (Extravio)')
                    AND motivo_da_ocorrencia IS NOT NULL
                    GROUP BY grupo ORDER BY n DESC LIMIT 10""",
                params,
            )
            if df_motivo.empty:
                st.info("Nenhuma ocorrência registrada nesse recorte.")
            else:
                fig = go.Figure(go.Bar(x=df_motivo["n"], y=df_motivo["grupo"], orientation="h", marker_color=COR_EXTRAVIO,
                                        text=[f"{v:,}".replace(",", ".") for v in df_motivo["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=380, margin=dict(l=10, r=65, t=10, b=10))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)
            st.caption("Coluna: `motivo_da_ocorrencia`, filtrado por `faixa_recebimento`")

        st.divider()

        # -------------------- Status --------------------
        st.markdown('<div class="section-title">📦 Status do Pacote / 运单状态</div>', unsafe_allow_html=True)
        df_status = run_query(
            f"""SELECT status_do_pacote AS grupo, count(*) AS n FROM public.backlog_atual {where_sql}
                {and_or_where} status_do_pacote IS NOT NULL GROUP BY grupo ORDER BY n DESC""",
            params,
        )
        with st.container(border=True):
            fig = go.Figure(go.Bar(x=df_status["n"], y=df_status["grupo"], orientation="h", marker_color=ANJUN_GREEN_DARK,
                                    text=[f"{v:,}".replace(",", ".") for v in df_status["n"]], textposition="outside", cliponaxis=False))
            fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#6B7A72"),
height=280, margin=dict(l=10, r=65, t=10, b=10))
            fig.update_yaxes(autorange="reversed", automargin=True)
            st.plotly_chart(fig, use_container_width=True)
        st.caption("Coluna: `status_do_pacote`")

        st.divider()

        # -------------------- Tabela de críticos --------------------
        st.markdown('<div class="section-title">🚨 Pacotes mais críticos (ordenado do mais parado pro menos) / 最紧急包裹</div>', unsafe_allow_html=True)
        criticos = run_query(
            f"""SELECT numero_do_waybill AS "Waybill", ponto_de_entrada AS "Ponto de Entrada",
                       supervisor AS "Supervisor", status_do_pacote AS "Status",
                       nome_do_cliente_merchant AS "Cliente Merchant",
                       motivo_da_ocorrencia AS "Motivo",
                       dias_sem_movimentacao AS "Dias sem mov.",
                       dias_desde_recebimento AS "Dias desde receb.",
                       faixa_recebimento AS "Faixa"
                FROM public.backlog_atual {where_sql}
                ORDER BY dias_desde_recebimento DESC NULLS LAST LIMIT 200""",
            params,
        )
        st.dataframe(criticos, use_container_width=True, hide_index=True)

# ===================== ABA VISÃO DSP =====================
with tab_dsp:
    snap_info2 = run_query("SELECT max(data_snapshot) AS ultimo FROM public.backlog")
    ultimo2 = snap_info2.iloc[0]["ultimo"] if not snap_info2.empty else None

    if ultimo2 is None:
        st.info("Base ainda vazia -- sobe um arquivo na aba Upload.")
    else:
        with st.container(key="dsp_view_bg"):
            st.markdown(
                f"""
                <div style="position:relative; overflow:hidden; background:linear-gradient(135deg, {ANJUN_GREEN_DARK} 0%, {ANJUN_GREEN} 100%);
                            border-radius:16px; padding:1.3rem 1.8rem; margin-bottom:1.3rem;
                            box-shadow:0 10px 24px rgba(0,77,38,0.22);">
                    <div style="position:absolute; inset:0; background-image:radial-gradient(rgba(255,255,255,0.12) 1px, transparent 1px);
                                background-size:16px 16px; opacity:0.5;"></div>
                    <div style="position:relative; z-index:1; display:flex; align-items:center; gap:1rem;">
                        <span style="background:white; border-radius:10px; padding:0.35rem 0.7rem; font-weight:800; color:{ANJUN_GREEN}; font-family:'Manrope',sans-serif;">Anjun</span>
                        <span style="color:white; font-size:1.5rem; font-weight:800; font-family:'Manrope',sans-serif;">
                            Backlog Diário {ultimo2:%d/%m} | 每日积压{ultimo2:%m}月{ultimo2:%d}日
                        </span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption(f"Regional: {', '.join(UFS_REGIONAL)} — apenas os estados que a Anjun responde nessa operação.")

            cor_faixa = {
                "0 a 4 dias": ANJUN_GREEN, "05 a 13 dias": "#D4A017",
                "14 a 20 dias (Crítico)": COR_CRITICO, "Mais de 20 (Extravio)": COR_EXTRAVIO,
            }
            ordem_faixa = list(cor_faixa.keys())
            uf_params = {"uf_regional": UFS_REGIONAL}
            total_dsp = run_query(
                "SELECT count(*) AS n FROM public.backlog_atual WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)",
                uf_params,
            ).iloc[0]["n"]

            r1c1, r1c2, r1c3, r1c4 = st.columns([1.2, 0.9, 1.1, 0.9])

            with r1c1, st.container(border=True):
                st.markdown('<div class="chart-title">RESPONSÁVEL / 负责人</div>', unsafe_allow_html=True)
                df = run_query(
                    """SELECT COALESCE(supervisor,'(sem resp.)') AS grupo, count(*) AS n
                       FROM public.backlog_atual WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s)
                       GROUP BY grupo ORDER BY n DESC""", uf_params,
                )
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=COR_EXTRAVIO,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
    height=260, margin=dict(l=10, r=30, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

            with r1c2, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG / 积压</div>', unsafe_allow_html=True)
                fig = go.Figure(go.Indicator(
                    mode="number", value=total_dsp,
                    number={"valueformat": ",", "font": {"size": 42, "color": COR_EXTRAVIO}},
                ))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
    height=260, margin=dict(l=10, r=10, t=30, b=5))
                st.plotly_chart(fig, use_container_width=True)

            with r1c3, st.container(border=True):
                st.markdown('<div class="chart-title">DIAS DE RECEBIMENTO / 收到后几天</div>', unsafe_allow_html=True)
                df = run_query(
                    """SELECT faixa_recebimento, count(*) AS n FROM public.backlog_atual
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND faixa_recebimento IS NOT NULL
                       GROUP BY faixa_recebimento""", uf_params,
                )
                df["ordem"] = df["faixa_recebimento"].apply(lambda x: ordem_faixa.index(x) if x in ordem_faixa else 99)
                df = df.sort_values("ordem")
                fig = go.Figure(go.Pie(labels=df["faixa_recebimento"], values=df["n"], hole=0.55,
                                        marker_colors=[cor_faixa.get(f, "#999") for f in df["faixa_recebimento"]],
                                        textinfo="value+percent", texttemplate="%{value:,}<br>(%{percent})",
                                        textposition="auto", insidetextorientation="horizontal"))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
    height=280, margin=dict(l=30, r=30, t=5, b=5),
                                   legend=dict(orientation="h", yanchor="bottom", y=-0.3, font=dict(size=9)))
                st.plotly_chart(fig, use_container_width=True)

            with r1c4, st.container(border=True):
                st.markdown('<div class="chart-title">POR ESTADO / 各州</div>', unsafe_allow_html=True)
                df = run_query(
                    """SELECT estado_do_ponto_de_entrada AS grupo, count(*) AS n FROM public.backlog_atual
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) GROUP BY grupo ORDER BY n DESC""",
                    uf_params,
                )
                fig = go.Figure(go.Bar(x=df["grupo"], y=df["n"], marker_color=COR_EXTRAVIO,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
    height=260, margin=dict(l=10, r=10, t=5, b=5))
                st.plotly_chart(fig, use_container_width=True)

            st.write("")
            r2c1, r2c2 = st.columns(2)
            with r2c1, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG POR DSP / 每个交付点的积压情况</div>', unsafe_allow_html=True)
                df = run_query(
                    """SELECT ponto_de_entrada AS grupo, count(*) AS n FROM public.backlog_atual
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND ponto_de_entrada IS NOT NULL
                       GROUP BY grupo ORDER BY n DESC LIMIT 15""", uf_params,
                )
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=COR_CRITICO,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
    height=460, margin=dict(l=10, r=65, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

            with r2c2, st.container(border=True):
                st.markdown('<div class="chart-title">BACKLOG POR ENTREGADOR / 按派送员</div>', unsafe_allow_html=True)
                df = run_query(
                    """SELECT entregador AS grupo, count(*) AS n FROM public.backlog_atual
                       WHERE estado_do_ponto_de_entrada = ANY(%(uf_regional)s) AND entregador IS NOT NULL
                       GROUP BY grupo ORDER BY n DESC LIMIT 15""", uf_params,
                )
                fig = go.Figure(go.Bar(x=df["n"], y=df["grupo"], orientation="h", marker_color=ANJUN_GREEN,
                                        text=[f"{v:,}".replace(",", ".") for v in df["n"]], textposition="outside", cliponaxis=False))
                fig.update_layout(                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#6B7A72"),
    height=460, margin=dict(l=10, r=65, t=5, b=5))
                fig.update_yaxes(autorange="reversed", automargin=True)
                st.plotly_chart(fig, use_container_width=True)

            st.caption(
                "Coluna: `entregador` (派送员) — ranking de quem tem mais backlog. Substitui o painel de Status "
                "do modelo de referência, que tinha rótulos trocados com dados de cliente merchant. "
                f"Todos os gráficos acima já filtram só a regional ({', '.join(UFS_REGIONAL)})."
            )