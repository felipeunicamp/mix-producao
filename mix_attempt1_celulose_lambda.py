import pyomo.environ as pyo
from pyomo.opt import SolverFactory
import pandas as pd
from collections import defaultdict
from typing import Dict,List,Tuple,Any
import os, boto3

_IS_LAMBDA = bool(os.environ.get('AWS_LAMBDA_FUNCTION_NAME'))
S3_BUCKET  = os.environ.get('S3_BUCKET', 'dashboard-mix-producao')
EXCEL_KEY  = os.environ.get('EXCEL_KEY', EXCEL_PATH)
JSON_KEY   = os.environ.get('JSON_KEY',  'dashboard_data.json')
EXCEL_PATH = '/tmp/simulador.xlsx' if _IS_LAMBDA else EXCEL_PATH

if _IS_LAMBDA:
    boto3.client('s3').download_file(S3_BUCKET, EXCEL_KEY, EXCEL_PATH)

# Lista de produtos
data1 = pd.read_excel(EXCEL_PATH, sheet_name='Produtividade', skiprows=2)
data2 = pd.read_excel(EXCEL_PATH, sheet_name='Total waste', skiprows=3)
data3 = pd.read_excel(EXCEL_PATH, sheet_name='Desclassificados', skiprows=2)
data4 = pd.read_excel(EXCEL_PATH, sheet_name='Reprocesso', skiprows=2)
data5 = pd.read_excel(EXCEL_PATH, sheet_name='Demanda', skiprows=2)
data6 = pd.read_excel(EXCEL_PATH, sheet_name='Produto por MP', skiprows=2)
data7 = pd.read_excel(EXCEL_PATH, sheet_name='Custos', skiprows=2)

#Processo para fazer o ajuste de produtos entre a aba demanda e a aba reprocesso
demanda_ajustada = pd.DataFrame(data5)
demanda_ajustada['Produto Ajustado'] = demanda_ajustada['Produto']
reprocesso_df = pd.DataFrame(data4)
reprocesso_df = reprocesso_df[['Produto vendável','Produto base']]
dict_reprocesso = reprocesso_df.set_index('Produto vendável')['Produto base'].to_dict()
demanda_ajustada['Produto Ajustado'] = demanda_ajustada['Produto Ajustado'].apply(lambda x: dict_reprocesso.get(x,x))

# Converter em sets (removendo valores NaN)
set_produtos_data1 = set(data1['Produto'].dropna())
set_produtos_data2 = set(data2['Produto'].dropna())
set_produtos_data3 = set(data3['Produto original'].dropna())
set_produtos_data32 = set(data3['Desclassificado'].dropna())
#set_produtos_data4 = set(data4['Produto vendável'].dropna()) # Apagar
#set_produtos_data42 = set(data4['Produto base'].dropna()) # Apagar
set_produtos_data5 = set(demanda_ajustada['Produto Ajustado'].dropna()) # Lista de demanda ajustada
set_produtos_data6 = set(data6['Produto'].dropna())
set_produtos_data7 = set(data7['Produto'].dropna())

# União de todos os sets para obter lista completa de produtos únicos
lista_produtos = set_produtos_data1.union(
    set_produtos_data2,
    set_produtos_data3,
    set_produtos_data32,
#    set_produtos_data4,   #Apagar
#    set_produtos_data42,  # Apagar
    set_produtos_data5,   # Ajustado anteriormente
    set_produtos_data6,
    set_produtos_data7
)

#Carregar Produtividade, Taxa de Performance e Produtividade Bruta
data = pd.read_excel(EXCEL_PATH,sheet_name='Produtividade',skiprows=2)
produtividade_df = pd.DataFrame(data)
produtividade = produtividade_df[['Produto','Máquina','Produtividade máxima (t/h)','Taxa PERF (Meta)']]
taxa_qual_por_maquina = {
    1: 1.0,    # MP1
    6: 1.0,    # MP6
    7: 1.0,    # MP7
    9: 1.0,    # MP9
    27: 1.0,   # MP27
    28: 1.0,   # MP28
    25: 1.0,   # MC25
    26: 1.0,   # MC26
    12: 1.0,   # MP12 (OTA)
    13: 1.0,   # MP13 (OTA)
    16: 1.0,   # MP16 (CP)
    23: 1.0,   # MP23 (CP)
}
produtividade['Taxa QUAL'] = produtividade['Máquina'].map(taxa_qual_por_maquina) # Adicionei a coluna Taxa QUAL para replicar a Taxa QUAL da

dict_produtividade = {}
dict_taxa_performance = {}
dict_taxa_qual = {}
dict_produtividade_bruta = {}

for index,row in produtividade.iterrows():
    produto = row['Produto']
    maquina = int(row['Máquina'])
    produtividade_max = float(row['Produtividade máxima (t/h)'])
    taxa_performance = float(row['Taxa PERF (Meta)'])
    taxa_qual = float(row['Taxa QUAL'])
    dict_produtividade[(maquina,produto)] = produtividade_max
    dict_taxa_performance[(maquina,produto)] = taxa_performance
    dict_taxa_qual[(maquina,produto)] = taxa_qual
    #dict_produtividade_bruta[(maquina,produto)] = taxa_qual*produtividade_max
    dict_produtividade_bruta[(maquina, produto)] = taxa_qual * produtividade_max * taxa_performance

#Carregar Total Waste - Condicional Flag_CBA:

param_refugo_cba = {'MA':False,'OR':False}
maquina_unidade = {1: 'MA', 6: 'MA', 7: 'MA', 9: 'MA', 27: 'OR', 28: 'OR', 25: 'OR', 26: 'OR', 12: 'OC', 13: 'OC', 16: 'CP', 23: 'CP'}

# 1. Preparar o DataFrame 'Total Waste' para modificação
total_waste_df = pd.DataFrame(data2) # Usando data2 que já foi lido
total_waste = total_waste_df[['Produto','Máquina','IAC','Refugo MP','Rep Externo - Perdas','MR2 - Perdas','Sala - Perdas','Cortadeira - Perdas Gramatura','Cortadeira - Perdas Cortadeira','Estoque - Perdas Expedição','Estoque - Perdas Refugo']].copy()

# Criar a coluna 'Refugo_MP_CBA' inicializada com 'Refugo MP' original
total_waste['Refugo_MP_CBA'] = total_waste['Refugo MP']

# 2. Preparar o DataFrame 'Desclassificados' para modificação
desclassificados_df = pd.DataFrame(data3)
desclassificados_df = desclassificados_df[['Produto original','Desclassificado','Máquina','Taxa']].copy()
# Adiciona a coluna 'Unidade' para facilitar a verificação da flag
desclassificados_df['Unidade'] = desclassificados_df['Máquina'].map(maquina_unidade)

# 3. Aplicar a lógica de ajuste do "Refugo MP" e zerar taxas de CBA
for index, row in desclassificados_df.iterrows():
    produto_original = row['Produto original']
    produto_desclassificado = row['Desclassificado']
    maquina = int(row['Máquina'])
    unidade = row['Unidade']
    taxa_desclassificacao_cba = float(row['Taxa'])  # Assumindo que 'Taxa' já é float ou pode ser convertida

    # Condições para aplicar a regra do CBA como refugo:
    # - O produto desclassificado começa com "CBA"
    # - O produto original está na lista de produtos a serem otimizados
    # - A unidade da máquina tem a flag 'param_refugo_cba' como True
    if (str(produto_desclassificado).startswith("CBA") and
            produto_original in lista_produtos and
            param_refugo_cba.get(unidade, False) == True):

        # Encontra a linha correspondente no total_waste (para obter o Refugo MP original)
        mask_target_total_waste = (total_waste['Máquina'] == maquina) & \
                                  (total_waste['Produto'] == produto_original)

        if not total_waste.loc[mask_target_total_waste].empty:
            taxa_refugo_mp_atual = float(total_waste.loc[mask_target_total_waste, 'Refugo MP'].values[0])

            # Recalcula o Refugo MP ajustado multiplicativamente
            taxa_refugo_mp_cba_atualizada = 1 - (1 - taxa_refugo_mp_atual) * (1 - taxa_desclassificacao_cba)

            # Atualiza a coluna 'Refugo_MP_CBA' no total_waste
            total_waste.loc[mask_target_total_waste, 'Refugo_MP_CBA'] = taxa_refugo_mp_cba_atualizada

            # Zera a taxa de desclassificação NO desclassificados_df para evitar dupla contagem.
            desclassificados_df.loc[index, 'Taxa'] = 0.0

#Carregar Total Waste (IAC, Refugo MP, Rep Externo - Perdas, MR2 - Perdas, Sala - Perdas, Cortadeira - Perdas Gramatura, Cortadeira - Perdas Cortadeira, Estoque - Perdas Expedição, Estoque - Perdas Refugo)

dict_IAC = {}
dict_Refugo_MP = {}
dict_Rep_Externo = {}
dict_MR2 = {}
dict_Sala_Perdas = {}
dict_Cortadeira_Perda_Gramatura = {}
dict_Cortadeira_Perdas_Cortadeira = {}
dict_Estoque_Perdas = {}
dict_Estoque_Perdas_Refugo = {}

for index,row in total_waste.iterrows():
    produto = row['Produto']
    maquina = int(row['Máquina'])
    IAC = float(row['IAC'])
    Refugo_MP = float(row['Refugo_MP_CBA']) #Está pegando da coluna atualizada considerando se CBA é refugo ou não
    Rep_Externo = float(row['Rep Externo - Perdas'])
    MR2 = float(row['MR2 - Perdas'])
    Sala_Perdas = float(row['Sala - Perdas'])
    Cortadeira_Perda_Gramatura = float(row['Cortadeira - Perdas Gramatura'])
    Cortadeira_Perdas_Cortadeira = float(row['Cortadeira - Perdas Cortadeira'])
    Estoque_Perdas = float(row['Estoque - Perdas Expedição'])
    Estoque_Perdas_Refugo = float(row['Estoque - Perdas Refugo'])

    dict_IAC[(maquina, produto)] = IAC
    dict_Refugo_MP[(maquina, produto)] = Refugo_MP
    dict_Rep_Externo[(maquina, produto)] = Rep_Externo
    dict_MR2[(maquina, produto)] = MR2
    dict_Sala_Perdas[(maquina, produto)] = Sala_Perdas
    dict_Cortadeira_Perda_Gramatura[(maquina, produto)] = Cortadeira_Perda_Gramatura
    dict_Cortadeira_Perdas_Cortadeira[(maquina, produto)] = Cortadeira_Perdas_Cortadeira
    dict_Estoque_Perdas[(maquina, produto)] = Estoque_Perdas
    dict_Estoque_Perdas_Refugo[(maquina, produto)] = Estoque_Perdas_Refugo

#Considerar as perdas por IAC e Refugo
dict_refugo_ajustado = {}
for (maquina,produto) in dict_Refugo_MP.keys():
    refugo_ajustado = 1 - (1 - dict_Refugo_MP[(maquina, produto)]) * dict_IAC.get((maquina, produto), 1)
    dict_refugo_ajustado[(maquina, produto)] = refugo_ajustado

#Considerar as perdas pelos demais fatores da aba Total Waste
"""
dict_waste = {}
for (maquina, produto) in dict_Refugo_MP.keys():
    waste_total = (
        dict_Rep_Externo.get((maquina, produto), 0) +
        dict_MR2.get((maquina, produto), 0) +
        dict_Sala_Perdas.get((maquina, produto), 0) +
        dict_Cortadeira_Perda_Gramatura.get((maquina, produto), 0) +
        dict_Cortadeira_Perdas_Cortadeira.get((maquina, produto), 0) +
        dict_Estoque_Perdas.get((maquina, produto), 0) +
        dict_Estoque_Perdas_Refugo.get((maquina, produto), 0)
    )
    dict_waste[(maquina, produto)] = waste_total
"""
dict_waste = {}
for (maquina, produto) in dict_Refugo_MP.keys():
    waste_total = 1 - (
        (1 - dict_Rep_Externo.get((maquina, produto), 0)) *
        (1 - dict_MR2.get((maquina, produto), 0)) *
        (1 - dict_Sala_Perdas.get((maquina, produto), 0)) *
        (1 - dict_Cortadeira_Perda_Gramatura.get((maquina, produto), 0)) *
        (1 - dict_Cortadeira_Perdas_Cortadeira.get((maquina, produto), 0)) *
        (1 - dict_Estoque_Perdas.get((maquina, produto), 0)) *
        (1 - dict_Estoque_Perdas_Refugo.get((maquina, produto), 0))
    )
    dict_waste[(maquina, produto)] = waste_total

#Fazer a combinação de IAC e Refugo com as demais perdas
dict_total_waste = {}
for (maquina, produto) in dict_Refugo_MP.keys():
    refugo_ajustado = dict_refugo_ajustado.get((maquina, produto), 0)
    waste = dict_waste.get((maquina, produto), 0)
    total_waste = 1 - (1 - refugo_ajustado) * (1 - waste)
    dict_total_waste[(maquina, produto)] = total_waste

#Carregar Desclassificados (Produto original, Desclassificado, Máquina e Taxa)

desclassificados_para_dict = desclassificados_df[['Produto original','Desclassificado','Máquina','Taxa']]
soma_taxas = desclassificados_para_dict.groupby(['Máquina','Produto original'])['Taxa'].sum().reset_index()
soma_taxas['Desclassificado'] = soma_taxas['Produto original']
soma_taxas['Taxa'] = 1 - soma_taxas['Taxa']
desclassificados_complementar = desclassificados_para_dict[['Produto original','Desclassificado','Máquina','Taxa']].copy()
desclassificados_complementar = pd.concat([desclassificados_complementar, soma_taxas[['Máquina', 'Produto original', 'Desclassificado', 'Taxa']]])
dict_taxa_desclassificado = desclassificados_complementar.set_index(['Máquina', 'Produto original', 'Desclassificado'])['Taxa'].to_dict()

# Produtos sem nenhuma desclassificação cadastrada — taxa complementar = 1.0
for (m, p) in dict_produtividade_bruta.keys():
    if (m, p, p) not in dict_taxa_desclassificado:
        dict_taxa_desclassificado[(m, p, p)] = 1.0
#Carregar Informações da aba Máquinas (Unidade, Máquina, Tempo de carga (h), Taxa DISP, Taxa QUAL)

maquinas = pd.read_excel(EXCEL_PATH, sheet_name='Máquinas', skiprows=2)
maquinas_df = pd.DataFrame(maquinas)
maquinas_df = maquinas_df[['Unidade','Máquina','Tempo de carga (h)','Taxa DISP','Taxa QUAL','Produção bruta máxima (t)']]

dict_tempo_carga = {}
dict_taxa_DISP = {}
dict_taxa_QUAL_MAQUINA = {}
dict_Prod_bruta_max = {}

for index,row in maquinas_df.iterrows():
    Unidade = row['Unidade']
    Maquina = int(row['Máquina'])
    Tempo_Carga = float(row['Tempo de carga (h)'])
    Taxa_Disp = float(row['Taxa DISP'])
    Taxa_QUAL = float(row['Taxa QUAL'])
    Prod_bruta_max = row['Produção bruta máxima (t)']
    if pd.isna(Prod_bruta_max):  # Verifica se o valor é NaN
        Prod_bruta_max = 1e9  # Atribui 1e9 para indicar "sem limite"
    else:
        Prod_bruta_max = float(Prod_bruta_max)  # Converte para float se não for NaN
    dict_tempo_carga[(Maquina)] = Tempo_Carga
    dict_taxa_DISP[(Maquina)] = Taxa_Disp
    dict_taxa_QUAL_MAQUINA[(Maquina)] = Taxa_QUAL
    dict_Prod_bruta_max[(Maquina)] = Prod_bruta_max

#Carregar Informações da aba Produto por MP:

produto_por_MP = pd.DataFrame(data6)
for _col_cp in ['MP16', 'MP23']:
    if _col_cp not in produto_por_MP.columns:
        produto_por_MP[_col_cp] = 0
produto_por_MP = produto_por_MP[['Produto','MP9','MP7','MP28','MP1','MP6','MP27','MC25','MC26','MP12','MP13','MP16','MP23']]
colunas_maquinas = ['MP9', 'MP7', 'MP28', 'MP1', 'MP6', 'MP27', 'MC25', 'MC26', 'MP12', 'MP13', 'MP16', 'MP23']
produto_por_MP_long = produto_por_MP.melt(id_vars=['Produto'],value_vars=colunas_maquinas,var_name='maquinas_str',value_name='flag_producao')
produto_por_MP_long['Máquina'] = produto_por_MP_long['maquinas_str'].str.replace('MP','').str.replace('MC','').astype(int)
produto_por_MP_long['flag_producao']=produto_por_MP_long['flag_producao'].astype(int)
produto_por_MP_long = produto_por_MP_long.drop(columns=['maquinas_str'])
dict_prod_por_MP = produto_por_MP_long.set_index(['Máquina','Produto'])['flag_producao'].to_dict()

#Carregar informações da Lista de Materiais

data8 = pd.read_excel(EXCEL_PATH, sheet_name='Lista de Materiais', skiprows=2)
Lista_Mat = pd.DataFrame(data8)
Lista_Mat = Lista_Mat[['Centro','Índice','Código','UMB','Material','Valor']]
Lista_Mat['Centro'] = Lista_Mat['Centro'].replace({'CP01': 'CP', 'OC01': 'OC'})
Lista_Mat_Filtered = Lista_Mat[(~Lista_Mat['Código'].astype(str).str.isnumeric())&
Lista_Mat['Índice'].isin(['Esp','Específico','Qtd'])&
~Lista_Mat['Material'].isin([
    'AGUA-TRATADA','ACIDO-SULF','VAPOR-MEDIO',
    'ENERG-MEDIA','ENERG-TERM','VAPOR-LICOR','AGUA-DESMI',
    'ENERG-PROP','AGUA-CAP1','ETE',
    'VAPOR-BIOMASSA','VAPOR-OLEO','BIOMASSA-F','CAV-ENERGIA',
    'DISPER-MP12','DISPER-MP13','DISPER-MP16','DISPER-MP23'
])].copy()

for index,row in Lista_Mat_Filtered.iterrows():
    if row['UMB'] == 'KG':
        Lista_Mat_Filtered.at[index, 'Valor'] = row['Valor'] / 1000

def explodir_lista_materiais(df: pd.DataFrame) -> Dict[Tuple[str, str], Dict[str, float]]:
    """
    Replica EXATAMENTE a função do código original lista_de_materiais.txt
    """
    def _recursive_explode(material: str, bom_map: defaultdict, factor: float = 1.0,
                           _visited: frozenset = frozenset()) -> Dict[str, float]:
        """Função interna recursiva com detecção de ciclos"""
        totals = defaultdict(float)
        componentes = bom_map.get(material, [])
        for sub_codigo, qty_direct in componentes:
            total_qty_to_add = qty_direct * factor
            totals[sub_codigo] += total_qty_to_add
            if sub_codigo in bom_map and sub_codigo not in _visited:
                sub_totals = _recursive_explode(sub_codigo, bom_map, total_qty_to_add, _visited | {material})
                for chave_sub, valor_sub in sub_totals.items():
                    totals[chave_sub] += valor_sub
        return dict(totals)

    bom_achatada_global = {}
    for centro in df['Centro'].unique():
        df_centro = df[df['Centro'] == centro]
        bom_map = defaultdict(list)
        for _, row in df_centro.iterrows():
            bom_map[row["Material"]].append((row["Código"], row["Valor"])) # 'Valor' já está ajustado
        produtos_raiz = set(df_centro["Material"])
        for produto_raiz in produtos_raiz:
            resultado_explosao = _recursive_explode(produto_raiz, bom_map, factor=1.0)
            if resultado_explosao:
                bom_achatada_global[(centro, produto_raiz)] = resultado_explosao
    return bom_achatada_global

# USO CORRETO:
# Esta é a linha que o otimizador executará (via lista_materiais.explodir_lista_materiais(self.lista_materiais))
dict_consumo_especifico = explodir_lista_materiais(Lista_Mat_Filtered)

#print(dict_consumo_especifico)

todas_fibras = set()
for (centro, produto), consumos in dict_consumo_especifico.items():
    todas_fibras.update(consumos.keys())

# 2. Função para obter consumo específico produto-fibra
def get_consumo_especifico(model, centro, produto, maq, fibra):
    """
    Replica a função consumo_especifico_produto_fibra do código original
    """
    # Reconstrói o nome do produto como aparece no Excel
    produto_ajustado = produto[:3] + str(maq).zfill(2) + produto[3:]
    chave_produto = (centro, produto_ajustado)
    return dict_consumo_especifico.get(chave_produto, {}).get(fibra, 0)

# 4. Função auxiliar para consumo de cavaco (se necessário)
def get_consumo_cavaco(model, centro, fibra, cavaco_madeira):
    """
    Replica a função consumo_especifico_cavaco do código original
    """
    chave_fibra = (centro, fibra)
    return dict_consumo_especifico.get(chave_fibra, {}).get(cavaco_madeira, 0)


#Carregamento de dados da aba Balanço de Fábrica MA:

#Específicos Evaporação e fibras
data10 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica MA', skiprows=3,usecols='B:E')
Esp_Evap_Fib = pd.DataFrame(data10).dropna()

#Parâmetros adicionais:
data11 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica MA', skiprows=3,usecols='G:I')
Param_add = pd.DataFrame(data11)

#Capacidade das plantas:
data12 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica MA', skiprows=3,usecols='K:P')
Capac_plantas = pd.DataFrame(data12).dropna()

#Constante de horas
horas_dia = 24
lista_madeira = ['EUCALIPTO','PINUS']
dict_digestor_MA = {'CKN-FC-K1': 'esco', 'CKN-FL-K2': 'kamyr', 'CKN-FC-K3': 'kamyr'}

#Dicionário de específicos evaporação e fibras:
dict_especificos_fibras_MA = {}
for index,row in Esp_Evap_Fib.iterrows():
    fibra = row['Fibra']
    tipo = row['Tipo']
    dict_especificos_fibras_MA[(fibra,tipo)]={'Proporção':row['Proporção'],
                                              'Concentração (%)':row['Concentração (%)']}

#Dicionário de Parâmetros adicionais:
dict_parametros_adicionais = Param_add.set_index('Parâmetro')['Valor'].to_dict()

#Dicionário Capacidade das Plantas:

dict_capacidade_MSR = Capac_plantas.set_index('Área.1')['Capacidade MSR'].to_dict()
dict_capacidade_Max = Capac_plantas.set_index('Área.1')['Capacidade Máx'].to_dict()
dict_capacidade_Rest = Capac_plantas.set_index('Área.1')['Capacidade restrição'].to_dict()
dict_dias_operacao = Capac_plantas.set_index('Área.1')['Dias operação'].to_dict()

# ── Diagnóstico de coeficientes de consumo específico ────────────────
print("\n" + "="*70)
print("DIAGNÓSTICO — COEFICIENTES DE CONSUMO ESPECÍFICO (Lista de Materiais)")
print("="*70)

# Fibras relevantes para as restrições de balanço
fibras_balanco_MA  = ['CKN-FC-K1', 'CKN-FL-K2', 'CKN-FC-K3', 'CTMP']
fibras_balanco_ORT = ['CKB-FC', 'CKB-FL']

# Coletar todos os coeficientes não-zero para essas fibras
coefs_MA  = []
coefs_ORT = []

for (centro, produto_lm), consumos in dict_consumo_especifico.items():
    for fibra, valor in consumos.items():
        if fibra in fibras_balanco_MA and centro == 'MA':
            coefs_MA.append((produto_lm, fibra, valor))
        if fibra in fibras_balanco_ORT and centro == 'ORT':
            coefs_ORT.append((produto_lm, fibra, valor))

# Estatísticas MA
if coefs_MA:
    vals_MA = [v for _, _, v in coefs_MA if v > 0]
    print(f"\n  MA — Digestores (CKN-FC-K1, CKN-FL-K2, CKN-FC-K3, CTMP):")
    print(f"    Total de coeficientes não-zero: {len(vals_MA)}")
    print(f"    Mínimo:  {min(vals_MA):.6f} t/t")
    print(f"    Máximo:  {max(vals_MA):.6f} t/t")
    print(f"    Média:   {sum(vals_MA)/len(vals_MA):.6f} t/t")
    print(f"    Ratio max/min: {max(vals_MA)/min(vals_MA):.1f}x")
    top5 = sorted(coefs_MA, key=lambda x: -x[2])[:5]
    print(f"    Top 5 maiores:")
    for prod, fib, val in top5:
        print(f"      {prod:<20} {fib:<15} {val:.6f}")
    bot5 = sorted([(p,f,v) for p,f,v in coefs_MA if v > 0], key=lambda x: x[2])[:5]
    print(f"    Top 5 menores (não-zero):")
    for prod, fib, val in bot5:
        print(f"      {prod:<20} {fib:<15} {val:.6f}")

# Estatísticas ORT
if coefs_ORT:
    vals_ORT = [v for _, _, v in coefs_ORT if v > 0]
    print(f"\n  ORT — Digestores (CKB-FC, CKB-FL):")
    print(f"    Total de coeficientes não-zero: {len(vals_ORT)}")
    print(f"    Mínimo:  {min(vals_ORT):.6f} t/t")
    print(f"    Máximo:  {max(vals_ORT):.6f} t/t")
    print(f"    Média:   {sum(vals_ORT)/len(vals_ORT):.6f} t/t")
    print(f"    Ratio max/min: {max(vals_ORT)/min(vals_ORT):.1f}x")
    top5 = sorted(coefs_ORT, key=lambda x: -x[2])[:5]
    print(f"    Top 5 maiores:")
    for prod, fib, val in top5:
        print(f"      {prod:<20} {fib:<15} {val:.6f}")
    bot5 = sorted([(p,f,v) for p,f,v in coefs_ORT if v > 0], key=lambda x: x[2])[:5]
    print(f"    Top 5 menores (não-zero):")
    for prod, fib, val in bot5:
        print(f"      {prod:<20} {fib:<15} {val:.6f}")

# Capacidades dos digestores para comparação de escala
print(f"\n  Limites das restrições (para comparar escala):")
print(f"    MA — Esco:   {dict_capacidade_MSR.get('Esco',  0):.2f} t/dia")
print(f"    MA — Kamyr:  {dict_capacidade_MSR.get('Kamyr', 0):.2f} t/dia")
print(f"    MA — CTMP:   {dict_capacidade_MSR.get('CTMP',  0):.2f} t/dia")

print("="*70)

#Lista de fibras:
fibras_MA = list(Esp_Evap_Fib['Fibra'].unique())

#Lista tipo fibras:
tipos_fibra_especificos_MA = list(Esp_Evap_Fib['Tipo'].unique())

#Lista Área.1 das plantas:
areas_balanco_MA = list(Capac_plantas['Área.1'].unique())

#Lista Parâmetros adicionais MA:
nomes_parametros_balanco_MA = list(dict_parametros_adicionais.keys())

#Carregamento dados de balanço de ORT:

#Tabela - Produção celulose e consumo fibras
data13 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica ORT', skiprows=3,usecols='B:E')
Prod_Cel_Fibras_ORT = pd.DataFrame(data13).dropna()
Prod_Cel_Fibras_ORT['Consumo_Anual_Fibras'] = Prod_Cel_Fibras_ORT['Consumo (t/dia)']*Prod_Cel_Fibras_ORT['Dias operação']
Prod_Cel_Fibras_ORT = Prod_Cel_Fibras_ORT.groupby('Fibra')['Consumo_Anual_Fibras'].sum().reset_index()

#Tabela Parâmetros adicionais
data14 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica ORT', skiprows=3,usecols='G:I')
Param_add_ORT = pd.DataFrame(data14).dropna()

data15 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica ORT', skiprows=3,usecols='K:P')
Capac_plantas_ORT = pd.DataFrame(data15).dropna()

data16 = pd.read_excel(EXCEL_PATH, sheet_name='Balanço fábrica ORT', skiprows=3,usecols='R:U')
Fibras_Digestores_ORT = pd.DataFrame(data16).dropna()

#Dicionário para Produção de celulose e consumo de fibras - CONSUMO ANUAL:
dict_consumo_fibras_ORT = Prod_Cel_Fibras_ORT.set_index('Fibra')['Consumo_Anual_Fibras'].to_dict()

#Dicionário para Parâmetros adicionais:
dict_param_add_ORT = Param_add_ORT.set_index('Parâmetro')['Valor'].to_dict()

#Dicionário para Capacidade das Plantas:
dict_emissario = Capac_plantas_ORT.set_index('Área.1')['Capacidade MSR'].to_dict()
dict_capacmax_ort = Capac_plantas_ORT.set_index('Área.1')['Capacidade Máx'].to_dict()
dict_dias_operacao = Capac_plantas_ORT.set_index('Área.1')['Dias operação.1'].to_dict()

#Dicionário para Fibras e Digestores:
dict_rendimento_ort = Fibras_Digestores_ORT.set_index('Fibra.1')['Rendimento (%)'].to_dict()
dict_carga_alcalina_ort = Fibras_Digestores_ORT.set_index('Fibra.1')['Carga alcalina (%)'].to_dict()

#Lista de fibras - Acrescentado em SETS:
# CKB-FC e CKB-FL vêm da tabela B:E; CKN-FC, CKN-FL e BCTMP são adicionadas
# explicitamente pois possuem SOLIDO-SECO na Lista de Materiais e alimentam o CDR
lista_fibras_ORT = list(Prod_Cel_Fibras_ORT['Fibra'].unique())
fibras_cdr_ORT = ['CKN-FC', 'CKN-FL', 'BCTMP']
for _f in fibras_cdr_ORT:
    if _f not in lista_fibras_ORT:
        lista_fibras_ORT.append(_f)

# Inverso: digestor → lista de fibras
dict_digestor = defaultdict(list)
for fibra, digestor in dict_digestor_MA.items():
    dict_digestor[digestor].append(fibra)
dict_digestor['ctmp'].append('CTMP')
for fibra in lista_fibras_ORT:
    dict_digestor[fibra].append(fibra)

#Lista de parâmetros adicionais - Acrescentando em SETS:
nomes_parametros_balanco_ORT = list(Param_add_ORT['Parâmetro'].unique())

#Lista de sets para Capacidade das Plantas:
nomes_area_capacPlantas = list(Capac_plantas_ORT['Área.1'].unique())

#Listas de fibras:
nomes_fibras_ort = Fibras_Digestores_ORT['Fibra.1'].unique()

#Carregamento dados da aba Balanço de Fábrica OTA:

#Carregamento dados da aba Balanço de Fábrica OTA — tabela MSR (colunas H:J) com fallback para B:E:

try:
    data_ota_msr = pd.read_excel(EXCEL_PATH,
                                  sheet_name='Balanço fábrica OTA', skiprows=5, usecols='H:J')
    data_ota_msr.columns = ['Restrição', 'Unidade', 'Valor']
    data_ota_msr = data_ota_msr.dropna(subset=['Restrição'])
    data_ota_msr['Valor'] = pd.to_numeric(data_ota_msr['Valor'], errors='coerce')
    dict_restricoes_OTA = data_ota_msr.set_index('Restrição')['Valor'].to_dict()
    if not dict_restricoes_OTA:
        raise ValueError('Tabela MSR OTA vazia')
except Exception:
    # Fallback: ler capacidades nominais de B:E (formato atual do Excel)
    data_ota_fb = pd.read_excel(EXCEL_PATH,
                                 sheet_name='Balanço fábrica OTA', skiprows=3, usecols='B:E')
    Capac_plantas_OTA = pd.DataFrame(data_ota_fb).dropna()
    Capac_plantas_OTA.columns = ['Area', 'Unidade', 'Capacidade_dia', 'Capacidade_h']
    Capac_plantas_OTA = Capac_plantas_OTA.set_index('Area')
    dict_restricoes_OTA = Capac_plantas_OTA['Capacidade_h'].to_dict()
    # Normalizar chave de Evaporação (soma das parcelas)
    _ev_keys = ['Pré Evaporação 1', 'Pré Evaporação 2', 'Evaporação 2', 'Evaporação 3']
    _ev_total = sum(float(Capac_plantas_OTA.loc[k, 'Capacidade_h']) for k in _ev_keys if k in Capac_plantas_OTA.index)
    dict_restricoes_OTA['Evaporação'] = _ev_total

# Limites operacionais OTA — valores em t/h ou m³/h conforme unidade
_lim_cozimento_OTA     = float(dict_restricoes_OTA.get('Cozimento', 1e9))
_lim_lavagem_L2_OTA    = float(dict_restricoes_OTA.get('Lavagem e Depuração - L2', 1e9))
_lim_lavagem_L4_OTA    = float(dict_restricoes_OTA.get('Lavagem e Depuração - L4', 1e9))
_lim_lavagem_OTA       = _lim_lavagem_L2_OTA + _lim_lavagem_L4_OTA
_lim_cdr4_OTA          = float(dict_restricoes_OTA.get('CDR4', 1e9))
_lim_caustificacao_OTA = float(dict_restricoes_OTA.get('Caustificação', 1e9))
_lim_evaporacao_OTA    = float(dict_restricoes_OTA.get('Evaporação', 1e9))

_BASE_SECA_OTA         = 0.93
_BASE_UMIDA_OTA        = 0.90
_PERDA_ETE_OTA         = 1.0015
_GER_SOLIDOS_OTA       = 0.936
_RECICLO_CINZAS_OTA    = 1.04
_CONS_CAV_TUMIDA_OTA   = 3.387
_BASE_SECA_CAV_OTA     = 0.432
_ALCALI_ATIVO_OTA      = 0.165
_CONC_LICOR_OTA        = 133.0
_CONC_SAIDA_LAV_OTA    = 0.115
_CONC_QUEIMA_CDR_OTA   = 0.68
_SPILL_OTA             = 1.08
_HORAS_ANO             = 365 * 24

#Carregamento dados da aba Balanço CP (aba ainda não existente → dicts vazios como fallback):

def _parse_msr(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 1e9

_CP_KEY_MAP = {
    'MP16 – Papel Extensível (t vapor/t papel enrolada)': 'MP16_extensivel_vapor',
    'MP16 – Papel Plano (t vapor/t pape enrolada)':      'MP16_plano_vapor',
    'MP23 – Papel Extensível (t vapor/t pape enrolada)': 'MP23_extensivel_vapor',
    'MP23 – Papel Plano (t vapor/t pape enrolada)':      'MP23_plano_vapor',
    'MP23 – Papel Desli (t vapor/t pape enrolada)':      'MP23_desli_vapor',
    'MP23 – Papel Branco (t vapor/t pape enrolada)':     'MP23_branco_vapor',
    'Cozimento (t vapor/ADt)':                           'cozimento_vapor_adt',
    'Deslignificação t vapor/ADt)':                      'deslignificacao_vapor_adt',
    'Evaporação (m³AE/t vapor)':                         'evaporacao_m3ae_vapor',
    'Stripper (t vapor/h)':                              'stripper_vapor_h',
    'Desaerador CF4':                                    'desaerador_cf4',
    'Desaerador CR2':                                    'desaerador_cr2',
    'Sopragem CR2':                                      'sopragem_cr2',
    'GNCD':                                              'gncd',
    'Outros':                                            'outros',
}

def _normalize_cp_key(desc):
    import re
    desc_strip = str(desc).strip()
    for original, norm in _CP_KEY_MAP.items():
        if original.strip() in desc_strip or desc_strip in original.strip():
            return norm
    return re.sub(r'[^a-z0-9_]', '_', desc_strip.lower())

try:
    # Tabela de restrições MSR (colunas G:J, cabeçalho na linha 5 do Excel = skiprows=4)
    data_cp_msr = pd.read_excel(EXCEL_PATH,
                                 sheet_name='Balanço CP', skiprows=4, usecols='G:J')
    data_cp_msr.columns = ['Restrição', 'Unidade', 'MSR', 'Dias']
    data_cp_msr = data_cp_msr.dropna(subset=['Restrição'])
    # Linhas com Dias numérico = restrições reais; Dias = '-' ou NaN = parâmetros de conversão
    _mask_restricao = pd.to_numeric(data_cp_msr['Dias'], errors='coerce').notna()
    data_cp_params  = data_cp_msr[~_mask_restricao].copy()
    data_cp_msr     = data_cp_msr[_mask_restricao].copy()
    data_cp_msr['MSR'] = data_cp_msr['MSR'].apply(_parse_msr)
    dict_restricoes_CP = data_cp_msr.set_index('Restrição')['MSR'].to_dict()
    dict_unidades_CP   = data_cp_msr.set_index('Restrição')['Unidade'].to_dict()
    dict_dias_CP       = data_cp_msr.set_index('Restrição')['Dias'].astype(float).to_dict()
    dict_params_CP     = data_cp_params.set_index('Restrição')['MSR'].to_dict()

    # Tabela de específicos por produto/processo (colunas L:O, cabeçalho na linha 4 do Excel = skiprows=3)
    data_cp_esp = pd.read_excel(EXCEL_PATH,
                                 sheet_name='Balanço CP', skiprows=3, usecols='L:O')
    data_cp_esp.columns = ['Descricao', 'Inverno', 'Verao', 'Media']
    data_cp_esp = data_cp_esp.dropna(subset=['Descricao'])
    dict_especificos_CP = {}
    for _, row in data_cp_esp.iterrows():
        chave = _normalize_cp_key(row['Descricao'])
        dict_especificos_CP[chave] = {
            'inverno': float(row['Inverno']) if pd.notna(row['Inverno']) else None,
            'verao':   float(row['Verao'])   if pd.notna(row['Verao'])   else None,
            'media':   float(row['Media'])   if pd.notna(row['Media'])   else None,
        }
    print(f"✓ Aba 'Balanço CP' carregada com sucesso.")
    print(f"  Restrições MSR: {list(dict_restricoes_CP.keys())}")
    print(f"  Parâmetros de conversão: {list(dict_params_CP.keys())}")
    print(f"  Específicos: {list(dict_especificos_CP.keys())}")
except Exception as _e_cp:
    print(f"⚠ Aba 'Balanço CP' não encontrada ({_e_cp}). Usando dicts vazios — adicionar aba ao Excel.")
    dict_restricoes_CP = {}
    dict_unidades_CP   = {}
    dict_dias_CP       = {}
    dict_params_CP     = {}
    dict_especificos_CP = {}

# ── Constantes de balanço — Fábrica CP ─────────────────────────────────────
# Hardcoded (não estão no Excel)
_BASE_SECA_CP         = 0.93     # t sólidos secos / t papel bruta
_BASE_UMIDA_CP        = 0.90     # base úmida celulose (ADt)
_PERDA_ETE_CP         = 1.00155  # fator de perda de fibra no efluente ETE
_BASE_SECA_CAVACO_CP  = 0.42     # base seca do cavaco
_CONC_LICOR_CP        = 110.0    # concentração licor branco (g/L)
_RECICLO_CINZAS_CP    = 1.05     # fator de reciclo de cinzas (CR2)
_CONC_SAIDA_LAV_CP    = 0.14     # concentração licor saída lavagens (14%)
_CONC_QUEIMA_CDR_CP   = 0.68     # concentração licor queima CDR (68%)
_SPILL_CP             = 1.08     # fator spill evaporação
_ETA_REPULP_CP        = 1.0     # eficiência de reaproveitamento do waste via pulper — altere aqui (0.0 = desativado, 1.0 = 100%)
_ETA_REPULP_EXT_CP    = 1.0     # eficiência de reaproveitamento do refugo externo (OTA→CP) via pulper — altere aqui

# Lidos do Excel — dict_params_CP
_PSA_MP16_CP          = float(dict_params_CP.get('%PSA MP16', 0.05))
_PSA_MP23_CP          = float(dict_params_CP.get('%PSA MP23', 0.05))
_ENROLADA_BRUTA_MP16  = float(dict_params_CP.get('%Enrolada para Bruta MP16', 0.035))
_ENROLADA_BRUTA_MP23  = float(dict_params_CP.get('%Enrolada para Bruta MP23', 0.03))
_GER_SOLIDOS_CKN_CP   = float(dict_params_CP.get('Geração Sólidos CKN', 1.34))
_GER_SOLIDOS_CKD_CP   = float(dict_params_CP.get('Geração de Sólidos CKD', 1.86))
_ALCALI_CKN_CP        = float(dict_params_CP.get('Álcali efetivo aplicado para CKN', 0.18))
_ALCALI_CKD_CP        = float(dict_params_CP.get('Álcali efetivo aplicado para CKD', 0.225))
_CAV_CKN_CP           = float(dict_params_CP.get('Consumo específico de cavacos para CKN', 4.1))
_CAV_CKD_CP           = float(dict_params_CP.get('Consumo específico de cavacos para CKD', 4.7))

# Específicos de vapor — coluna Média Ponderada de dict_especificos_CP
def _esp_media(chave):
    return dict_especificos_CP.get(chave, {}).get('media') or 0.0

# Limites MSR — lidos do Excel
_lim_cozimento_CP      = float(dict_restricoes_CP.get('Cozimento', 1e9))
_lim_lavagem1_CP       = float(dict_restricoes_CP.get('Lavagem 1', 1e9))
_lim_lavagem2_CP       = float(dict_restricoes_CP.get('Lavagem 2', 1e9))
_lim_deslignif_CP      = float(dict_restricoes_CP.get('Deslignificação', 1e9))
_lim_pulper_CP         = float(dict_restricoes_CP.get('Pulper', 1e9))
_lim_cr2_CP            = float(dict_restricoes_CP.get('CR2', 1e9))
_lim_caustificacao_CP  = float(dict_restricoes_CP.get('Caustificação', 1e9))
_lim_evaporacao_CP     = float(dict_restricoes_CP.get('Evaporação', 1e9))

# Dias de operação CP
_DIAS_CP = float(dict_dias_CP.get('Cozimento', 355))

# Máquinas CP
_MAQUINAS_CP = [16, 23]

# Prefixos por tipo de celulose — usados para filtrar model.produtos
_PREFIXOS_CKN_CP = ('SAK', 'SLF', 'SLP', 'SLR', 'SAD', 'SDT', 'SKS', 'SSN')
_PREFIXOS_CKD_CP = ('SDK', 'SPD')
_PREFIXOS_CKB_CP = ('STK', 'SBK', 'SPB', 'STB')

## Funções de cálculo das restrições de balanço — Fábrica OTA

def calc_prod_bruta_OTA_h(model):
    """Produção bruta total de MP12 + MP13 em t papel/h."""
    return sum(
        model.producao_bruta[p, m]
        / (dict_tempo_carga.get(m, _HORAS_ANO) * dict_taxa_DISP.get(m, 1.0))
        for p in model.produtos
        for m in [12, 13]
    )

def calc_lavagem_OTA(model):
    """Demanda de Celulose Lavagem (ADt/h)."""
    prod_h = calc_prod_bruta_OTA_h(model)
    return (prod_h * _BASE_SECA_OTA / _BASE_UMIDA_OTA) * _PERDA_ETE_OTA

def calc_cozimento_OTA(model):
    """Demanda de Celulose Cozimento (ADt/h) — mesma fórmula que lavagem."""
    prod_h = calc_prod_bruta_OTA_h(model)
    return (prod_h * _BASE_SECA_OTA / _BASE_UMIDA_OTA) * _PERDA_ETE_OTA

def calc_cdr_OTA(model):
    """Sólidos Queima CDR4 (tSS/h)."""
    return calc_lavagem_OTA(model) * _GER_SOLIDOS_OTA * _RECICLO_CINZAS_OTA

def calc_caustificacao_OTA(model):
    """Demanda de Licor Branco — Caustificação (m³/h)."""
    return (
        calc_cozimento_OTA(model)
        * _CONS_CAV_TUMIDA_OTA
        * _BASE_SECA_CAV_OTA
        * _ALCALI_ATIVO_OTA
        / _CONC_LICOR_OTA
    ) * 1000

def calc_evaporacao_OTA(model):
    """Demanda de Evaporação (tAE/h)."""
    tss_h = calc_lavagem_OTA(model) * _GER_SOLIDOS_OTA

    # Parte 1: (TSS/conc_saída_lavagem − TSS) × Spill
    parte1 = (tss_h / _CONC_SAIDA_LAV_OTA - tss_h) * _SPILL_OTA

    # Parte 2: TSS − TSS/conc_queima_CDR
    parte2 = tss_h - (tss_h / _CONC_QUEIMA_CDR_OTA)

    return parte1 + parte2

## ── Funções de balanço — Fábrica CP ───────────────────────────────────────

def _prod_h_CP(model, prefixos, maquinas):
    """Produção bruta horária (t/h) filtrada por prefixo e máquina."""
    return sum(
        model.producao_bruta[p, m]
        for p in model.produtos
        for m in maquinas
        if str(p).startswith(prefixos)
    ) / (_DIAS_CP * 24)

def _to_adt_h(prod_h):
    """Converte t/h papel bruta → ADt/h celulose."""
    return prod_h * _BASE_SECA_CP / _BASE_UMIDA_CP * _PERDA_ETE_CP

_MAQUINAS_REFUGO_EXT_CP = [6, 7, 9, 27, 28, 25, 26, 12, 13]  # MA (sem MP1) + OR + OTA

def _calc_refugo_ext_disponivel_adt_h(model, maquinas=None):
    """Fibra disponível do refugo de outras unidades para o pulper CP, em ADt/h.
    Limitado pela produção real de cada unidade (que é limitada pelas horas disponíveis de suas máquinas).
    Conversão: t papel refugo → ADt fibra usando base_seca/base_umida de CP × eficiência do pulper."""
    if maquinas is None:
        maquinas = _MAQUINAS_REFUGO_EXT_CP
    return sum(
        model.producao_bruta[p, m]
        * dict_Refugo_MP.get((m, p), 0)
        * (_BASE_SECA_CP / _BASE_UMIDA_CP)
        * _ETA_REPULP_EXT_CP
        / (_DIAS_CP * 24)
        for p in model.produtos
        for m in maquinas
        if dict_Refugo_MP.get((m, p), 0) > 0
    )

def _calc_waste_repulp_adt_h(model, prefixos, maquinas):
    """ADt/h de fibra recuperada do waste de CP via pulper, para os tipos e máquinas informados.
    Inclui toda perda bruta→líquida (refugo) e líquida→vendável (waste) via dict_total_waste.
    A fibra recuperada não passa pelos digestores nem pela lavagem — não gera carga no CR2."""
    return sum(
        model.producao_bruta[p, m]
        * dict_total_waste.get((m, p), 0)
        * (_BASE_SECA_CP / _BASE_UMIDA_CP)
        * _ETA_REPULP_CP
        / (_DIAS_CP * 24)
        for p in model.produtos
        for m in maquinas
        if str(p).startswith(prefixos)
        and dict_prod_por_MP.get((m, p), 0) == 1
    )

def calc_cozimento_CP(model):
    """Demanda de Cozimento CP (ADt/h) — CKN + CKD, descontando fibra reaproveitada do waste interno e refugo externo."""
    prod_h = _prod_h_CP(model, _PREFIXOS_CKN_CP + _PREFIXOS_CKD_CP, _MAQUINAS_CP)
    repulp = (_calc_waste_repulp_adt_h(model, _PREFIXOS_CKN_CP, _MAQUINAS_CP)
              + _calc_waste_repulp_adt_h(model, _PREFIXOS_CKD_CP, _MAQUINAS_CP))
    return _to_adt_h(prod_h) - repulp - _refugo_ext_cp_total(model)

def calc_lavagem1_CP(model):
    """Demanda Lavagem 1 CP (ADt/h) — CKN, descontando waste interno e refugo externo (CKN-compatível)."""
    prod_h = _prod_h_CP(model, _PREFIXOS_CKN_CP, _MAQUINAS_CP)
    repulp = _calc_waste_repulp_adt_h(model, _PREFIXOS_CKN_CP, _MAQUINAS_CP)
    return _to_adt_h(prod_h) - repulp - _refugo_ext_cp_total(model)

def calc_lavagem2_CP(model):
    """Demanda Lavagem 2 CP (ADt/h) — CKD de MP23, descontando fibra CKD reaproveitada do waste."""
    prod_h = _prod_h_CP(model, _PREFIXOS_CKD_CP, [23])
    repulp = _calc_waste_repulp_adt_h(model, _PREFIXOS_CKD_CP, _MAQUINAS_CP)
    return _to_adt_h(prod_h) - repulp

def calc_pulper_CP(model):
    """Demanda Pulper IP40 CP (ADt/h) — CKB (MP23) + PSA (CKN) + waste interno (CKN+CKD+CKB) + refugo externo OTA."""
    prod_h_ckb      = _prod_h_CP(model, _PREFIXOS_CKB_CP, [23])
    adt_h_ckb       = _to_adt_h(prod_h_ckb)
    prod_h_psa_mp16 = _prod_h_CP(model, _PREFIXOS_CKN_CP, [16]) * _PSA_MP16_CP
    prod_h_psa_mp23 = _prod_h_CP(model, _PREFIXOS_CKN_CP, [23]) * _PSA_MP23_CP
    adt_h_psa       = _to_adt_h(prod_h_psa_mp16 + prod_h_psa_mp23)
    repulp_total    = (
        _calc_waste_repulp_adt_h(model, _PREFIXOS_CKN_CP, _MAQUINAS_CP)
        + _calc_waste_repulp_adt_h(model, _PREFIXOS_CKD_CP, _MAQUINAS_CP)
        + _calc_waste_repulp_adt_h(model, _PREFIXOS_CKB_CP, _MAQUINAS_CP)
    )
    return adt_h_ckb + adt_h_psa + repulp_total + _refugo_ext_cp_total(model)

def calc_cr2_CP(model):
    """Sólidos CR2 CP (tss/dia)."""
    tss_h = (calc_lavagem1_CP(model) * _GER_SOLIDOS_CKN_CP
           + calc_lavagem2_CP(model) * _GER_SOLIDOS_CKD_CP)
    return tss_h * _RECICLO_CINZAS_CP * 24

def calc_caustificacao_CP(model):
    """Demanda de Licor Branco — Caustificação CP (m³ Lb/dia)."""
    licor_h = (
        calc_lavagem1_CP(model) * _CAV_CKN_CP * _ALCALI_CKN_CP
        + calc_lavagem2_CP(model) * _CAV_CKD_CP * _ALCALI_CKD_CP
    ) * _BASE_SECA_CAVACO_CP / _CONC_LICOR_CP * 1000
    return licor_h * 24

def calc_agua_evap_CP(model):
    """Água a evaporar do licor preto CP (tAE/h)."""
    tss_h = (calc_lavagem1_CP(model) * _GER_SOLIDOS_CKN_CP
           + calc_lavagem2_CP(model) * _GER_SOLIDOS_CKD_CP)
    parte_saida = (tss_h / _CONC_SAIDA_LAV_CP - tss_h) * _SPILL_CP
    parte_cdr   =  tss_h / _CONC_QUEIMA_CDR_CP - tss_h
    return parte_saida - parte_cdr

def _get_ce_mp16(produto):
    """CE de vapor de MP16 por produto (t vapor / t papel enrolada) — Média Ponderada."""
    p = str(produto)
    if any(p.startswith(x) for x in ('SLF', 'SLP', 'SLR')):
        return _esp_media('MP16_plano_vapor')
    if p.startswith(_PREFIXOS_CKN_CP):
        return _esp_media('MP16_extensivel_vapor')
    return 0.0

def _get_ce_mp23(produto):
    """CE de vapor de MP23 por produto (t vapor / t papel enrolada) — Média Ponderada."""
    p = str(produto)
    if p.startswith(_PREFIXOS_CKD_CP):
        return _esp_media('MP23_desli_vapor')
    if p.startswith(_PREFIXOS_CKB_CP):
        return _esp_media('MP23_branco_vapor')
    if any(p.startswith(x) for x in ('SLF', 'SLP', 'SLR')):
        return _esp_media('MP23_plano_vapor')
    return _esp_media('MP23_extensivel_vapor')

def calc_evaporacao_CP(model):
    """Demanda de vapor total CP (tv/h) ≤ MSR Evaporação."""
    economia_evap = _esp_media('evaporacao_m3ae_vapor')

    ce_mp16 = sum(
        (model.producao_bruta[p, 16] / (_DIAS_CP * 24))
        * (1 - _ENROLADA_BRUTA_MP16)
        * _get_ce_mp16(p)
        for p in model.produtos
        if str(p).startswith(_PREFIXOS_CKN_CP)
        and dict_prod_por_MP.get((16, p), 0) == 1
    )

    ce_mp23 = sum(
        (model.producao_bruta[p, 23] / (_DIAS_CP * 24))
        * (1 - _ENROLADA_BRUTA_MP23)
        * _get_ce_mp23(p)
        for p in model.produtos
        if str(p).startswith(_PREFIXOS_CKN_CP + _PREFIXOS_CKD_CP + _PREFIXOS_CKB_CP)
        and dict_prod_por_MP.get((23, p), 0) == 1
    )

    ce_coz        = _esp_media('cozimento_vapor_adt')
    ce_deslig     = _esp_media('deslignificacao_vapor_adt')
    t_stripper    = _esp_media('stripper_vapor_h')
    t_desaer_cf4  = _esp_media('desaerador_cf4')
    t_desaer_cr2  = _esp_media('desaerador_cr2')
    t_sopragem    = _esp_media('sopragem_cr2')
    t_gncd        = _esp_media('gncd')
    t_outros      = _esp_media('outros')

    agua_evap = calc_agua_evap_CP(model)
    evap_term = agua_evap / economia_evap if economia_evap > 0 else 0

    return (
        ce_mp16
        + ce_mp23
        + ce_coz    * calc_cozimento_CP(model)
        + ce_deslig * calc_lavagem2_CP(model)
        + evap_term
        + t_stripper
        + t_desaer_cf4
        + t_desaer_cr2
        + t_sopragem
        + t_gncd
        + t_outros
    )

#Carregar dados da aba Custos:
data17 = pd.read_excel(EXCEL_PATH, sheet_name='Custos', skiprows=2)
dados_custos = pd.DataFrame(data17)
dados_custos = dados_custos[['Produto','Máquina','Custo Variavel ']].dropna()

#Dicionário custos:
dict_custos = dados_custos.set_index(['Produto','Máquina'])['Custo Variavel '].to_dict()

# Recalcular custos de desclassificados com base no custo ponderado dos produtos originais.
# Cobre dois casos: (1) sem custo cadastrado para a máquina geradora e (2) custo diferente do original.
# Fonte: data3 (taxa original, sem zeramentos do ajuste CBA).
_desc_para_custo = pd.DataFrame(data3)[['Produto original', 'Desclassificado', 'Máquina', 'Taxa']].dropna()
_desc_para_custo = _desc_para_custo[_desc_para_custo['Desclassificado'] != _desc_para_custo['Produto original']]

for (desc, maq), grp in _desc_para_custo.groupby(['Desclassificado', 'Máquina']):
    pares_com_custo = [
        (row['Produto original'], row['Taxa'])
        for _, row in grp.iterrows()
        if dict_custos.get((row['Produto original'], maq)) is not None
    ]
    if not pares_com_custo:
        continue
    soma_taxas = sum(t for _, t in pares_com_custo)
    if soma_taxas == 0:
        continue
    custo_ponderado = sum(t * dict_custos[(orig, maq)] for orig, t in pares_com_custo) / soma_taxas
    dict_custos[(desc, maq)] = custo_ponderado

#Carregar dados da aba Demanda:
data18 = pd.read_excel(EXCEL_PATH, sheet_name='Demanda', skiprows=2)
dados_demanda = pd.DataFrame(data18)
dados_demanda = dados_demanda[['Mercado','Produto','Quantidade (TO)','Preço']]
data19 = pd.read_excel(EXCEL_PATH, sheet_name='Reprocesso', skiprows=2)
dados_reprocesso = pd.DataFrame(data19)
dados_reprocesso = dados_reprocesso[['Produto vendável','Produto base']]
#dict_reprocesso = dados_reprocesso.set_index('Produto base')['Produto vendável'].to_dict()
dict_reprocesso = dados_reprocesso.set_index('Produto vendável')['Produto base'].to_dict()

for index, row in dados_demanda.iterrows():
    produto = row['Produto']
    if produto in dict_reprocesso:
        dados_demanda.at[index, 'Produto'] = dict_reprocesso[produto]

#Dicionáro

dict_demanda = dados_demanda.groupby(['Produto', 'Mercado'])['Quantidade (TO)'].sum().to_dict()
dict_preco = dados_demanda.set_index(['Produto', 'Mercado'])['Preço'].to_dict() #IMPORTANTE - NÃO PODE HAVER DUPLICATAS PARA O MESMO PRODUTO, MESMO MERCADO NA ABA DEMANDAS
#dict_preco = dados_demanda.groupby(['Produto', 'Mercado']).apply(
#    lambda x: (x['Preço'] * x['Quantidade (TO)']).sum() / x['Quantidade (TO)'].sum()
#).to_dict()

#Carregador dados da aba Parâmetros:

data20 = pd.read_excel(EXCEL_PATH, sheet_name='Parâmetros', skiprows=2)
parametros1 = pd.DataFrame(data20)
parametros1 = parametros1[['Parâmetro','Valor']]

# Remove linhas com Parâmetro NaN ou que são cabeçalhos intermediários
parametros1 = parametros1.dropna(subset=['Parâmetro'])
parametros1 = parametros1[parametros1['Parâmetro'] != 'Configurações do modelo:']

#dicionário Parâmetros:
dict_param_modelo = parametros1.set_index('Parâmetro')['Valor'].to_dict()

# Parâmetros de remuneração e priorização — lidos do dict
flag_remuneracao_celulose = dict_param_modelo.get('Incluir remuneração da celulose', 0)

cambio = dict_param_modelo.get('Cambio', 1)
custo_variavel_fibra_curta = dict_param_modelo.get('Custo Variavel Celulose FC', 0)
custo_variavel_fibra_longa = dict_param_modelo.get('Custo Variavel Celulose FL', 0)
preco_venda_fibra_curta = dict_param_modelo.get('Preço de Cel. Merc. ME FC', 0) * cambio
preco_venda_fibra_longa = dict_param_modelo.get('Preço de Cel. Merc. ME FF', 0) * cambio

dict_penalidade_ociosidade = {
    7:  dict_param_modelo.get('Priorizar produção MP7', 0) * 1e5,
    9:  dict_param_modelo.get('Priorizar produção MP9', 0) * 1e5,
    28: dict_param_modelo.get('Priorizar produção MP28', 0) * 1e5,
    1:  0,
    6:  0,
    27: 0,
    25: 0,
    26: 0,
    12: 0,
    13: 0,
    16: 0,
    23: 0,
}

lista_parametros = list(parametros1['Parâmetro'])

###### Cálculo do Consumo de Fibras:

def calc_consumo_fibra(model, centro, fibra):
    fibras_do_centro = {'MA': fibras_MA, 'ORT': lista_fibras_ORT}
    if fibra not in fibras_do_centro.get(centro, []):
        return 0
    consumo = sum(
        model.producao_bruta[p, m] * get_consumo_especifico(model,centro, p, m, fibra)
        for p in model.produtos
        for m in model.maquinas
    )
    # ORT: soma consumo fixo de celulose (não depende do mix de papel)
    if centro == 'ORT':
        consumo += dict_consumo_fibras_ORT.get(fibra, 0)
    # MA: ajusta pela perda de fibra
    perda = dict_parametros_adicionais.get('Perda de fibras (%)', 0) if centro == 'MA' else 0
    return consumo / (1 - perda)

### Função para cálculo de TSS:

def calc_tss_cinzas(model, centro, fibra):
    geracao_tss = dict_consumo_especifico.get((centro, fibra), {}).get('SOLIDO-SECO', 0)
    entrada_cinzas = dict_parametros_adicionais.get('Entrada cinzas (%)', 0)
    return -1 * geracao_tss * calc_consumo_fibra(model, centro, fibra) * (1 + entrada_cinzas) * 0.9

def calc_tss_total(model, centro):
    dias = 365  # fallback — MA não tem dias por área no dict
    return sum(calc_tss_cinzas(model, centro, f) for f in fibras_MA) / dias

## Cálculo de evaporação

def calc_evaporacao(model):
    centro = 'MA'
    total_evap = 0
    dias = 365
    for fibra in fibras_MA:
        tss_hora = calc_tss_cinzas(model, centro, fibra) / dias / horas_dia
        tipos_da_fibra = [tipo for (f, tipo) in dict_especificos_fibras_MA.keys() if f == fibra]
        for tipo in tipos_da_fibra:
            esp = dict_especificos_fibras_MA[(fibra, tipo)]
            proporcao = esp['Proporção']
            concentracao = esp['Concentração (%)']
            concentracao_queima = dict_parametros_adicionais['Concentração queima (%)']
            total_evap += (tss_hora * proporcao / concentracao) - (tss_hora * proporcao / concentracao_queima)
    spill = dict_parametros_adicionais['Spill (m3/h)']
    return spill + total_evap

## Cálculo de Licor verde

def calc_licor_verde(model):
    centro = 'MA'
    total_LB = 0
    dias = 365
    for fibra in fibras_MA:
        consumo_dia = 0.9 * calc_consumo_fibra(model, centro, fibra) / dias
        esp_licor = dict_consumo_especifico.get((centro, fibra), {}).get('LICOR-BRANCO', 0)
        teor_alcali = dict_parametros_adicionais['Alcali Licor Branco / Verde']
        total_LB += consumo_dia * (esp_licor / horas_dia) / (teor_alcali / 1000)
    eficiencia = dict_parametros_adicionais['Eficiência Licor Verde (%)']
    return total_LB / eficiencia

## Cálculo PMAD

def calc_pmad(model):
    centro = 'MA'
    total = 0
    dias = 365
    for madeira, chave_perda in [('EUCALIPTO', 'Perda Euca (%)'), ('PINUS', 'Perda Pinus (%)')]:
        cavaco = f'CAV-{madeira}'
        consumo_cav = sum(
            calc_consumo_fibra(model, centro, fibra) * get_consumo_cavaco(model, centro, fibra, cavaco)
            for fibra in fibras_MA
        )
        perda = dict_parametros_adicionais.get(chave_perda, 0)
        total += (consumo_cav / (1 - perda)) / dias
    return total

## Cálculo Disper-MPC

def calc_disper(model):
    return sum(
        model.producao_bruta[p, m] * get_consumo_especifico(model, 'MA', p, m, 'DISPER-MPC')
        for p in model.produtos
        for m in model.maquinas
    )


## Cálculo Produção por Digestor

def calc_producao_digestor_MA(model, digestor):
    centro = 'MA'
    fibras_digestor = dict_digestor.get(digestor, [])
    dias = 365
    return sum(
        calc_consumo_fibra(model, centro, fibra) / dias
        for fibra in fibras_digestor
        if fibra in fibras_MA
    )

## Cálculo ORT - Caustificação

def calc_caustificacao(model):
    centro = 'ORT'
    licor_branco = 0
    dias = dict_dias_operacao.get('Caustificação', 365)
    for fibra in lista_fibras_ORT:
        rendimento = dict_rendimento_ort.get(fibra, 1)
        carga_alcalina = dict_carga_alcalina_ort.get(fibra, 0)
        if 'CKB' in fibra:
            concentracao = dict_param_add_ORT.get('Concentração caust 1 (g/l NaOH)', 1) / 1000
            perda = dict_param_add_ORT.get('Perda de fibra branca (%)', 0)
            consumo_dia = calc_consumo_fibra(model, centro, fibra) / (1 - perda) / dias
        else:
            concentracao = dict_param_add_ORT.get('Concentração caust 2 (g/l NaOH)', 1) / 1000
            consumo_dia = calc_consumo_fibra(model, centro, fibra) / dias
        licor_branco += 0.9 * consumo_dia * carga_alcalina / rendimento / concentracao

    dias_deslig = dict_dias_operacao.get('Caustificação', 365)
    deslig_euca = calc_consumo_fibra(model, 'ORT', 'CKB-FC') * dict_param_add_ORT.get('Deslignificação Euca (m3/tsa)', 0) / dias_deslig
    deslig_pinus = calc_consumo_fibra(model, 'ORT', 'CKB-FL') * dict_param_add_ORT.get('Deslignificação Pinus (m3/tsa)', 0) / dias_deslig
    licor_angatuba = dict_param_add_ORT.get('Licor branco Angatuba (m3 LB/d)', 0)

    return licor_branco + deslig_euca + deslig_pinus + licor_angatuba

## Cálculo ORT Efluentes

def calc_captacao(model):
    prod_papeis = sum(
        model.producao_bruta[p, m]
        for p in model.produtos
        for m in [25, 26, 27, 28]
    )
    prod_cel = sum(dict_consumo_fibras_ORT.get(f, 0) for f in lista_fibras_ORT)
    dias = dict_dias_operacao.get('Outorga Captação', 365)
    captacao = dict_param_add_ORT.get('Captação de Água (m3/t)', 0)
    return captacao * (prod_papeis + prod_cel) / horas_dia / dias

def calc_emissario(model):
    prod_papeis = sum(
        model.producao_bruta[p, m]
        for p in model.produtos
        for m in [25, 26, 27, 28]
    )
    prod_cel = sum(dict_consumo_fibras_ORT.get(f, 0) for f in lista_fibras_ORT)
    dias = dict_dias_operacao.get('Outorga Emissario', 365)
    emissario = dict_param_add_ORT.get('Emissário (m3/t)', 0)
    return emissario * (prod_papeis + prod_cel) / horas_dia / dias

## Cálculo - ORT Produção por digestor

def calc_producao_digestor_ORT(model, digestor):
    centro = 'ORT'
    fibras_digestor = dict_digestor.get(digestor, [])
    dias = dict_dias_operacao.get(digestor, 365)
    return sum(
        calc_consumo_fibra(model, centro, fibra) / dias
        for fibra in fibras_digestor
        if fibra in lista_fibras_ORT
    )

## Cálculo ORT — CDR (Caldeira de Recuperação)
# TSS total = soma dos sólidos secos gerados por cada fibra PRODUZIDA nos digestores ORT.
# O coeficiente SOLIDO-SECO na LM é negativo (convenção: negativo = gerado).
# Dividimos pelos dias de operação para converter anual → diário (unidade do limite: tss/d).
#
# IMPORTANTE: iteramos apenas sobre MC25 e MC26 (máquinas de celulose ORT).
# MP27 e MP28 consomem fibra já processada como insumo — não geram licor novo,
# portanto não devem entrar no CDR.

#maquinas_celulose_ORT = [25, 26]

def calc_tss_fibra_ORT(model, fibra):
    """TSS anual gerado pela fibra, considerando apenas MC25 e MC26."""
    centro = 'ORT'
    coef_ss = dict_consumo_especifico.get((centro, fibra), {}).get('SOLIDO-SECO', 0)
    #consumo_digestor = sum(
    #    model.producao_bruta[p, m] * get_consumo_especifico(model, centro, p, m, fibra)
    #    for p in model.produtos
    #    for m in maquinas_celulose_ORT
    #)
    # coef_ss negativo = gerado; -1 x coef_ss = geracao positiva
    #return -1 * coef_ss * consumo_digestor
    return -1 * coef_ss * calc_consumo_fibra(model, 'ORT', fibra)

def calc_cdr(model):
    fibras_cdr = ['CKB-FC', 'CKB-FL', 'CKN-FC', 'CKN-FL', 'BCTMP']
    dias = dict_dias_operacao.get('CDR', 365)
    total_tss = sum(calc_tss_fibra_ORT(model, fibra) for fibra in fibras_cdr)

    # TSS de Angatuba — licor preto externo recebido diariamente
    # volume (m³/d) × concentração (t_ss/m³) = t_ss/dia
    volume_angatuba = dict_param_add_ORT.get('Volume recebido de Licor Preto de Angatuba (m³/d)', 0)
    conc_angatuba   = dict_param_add_ORT.get('Concentração Licor Preto recebido de Angatuba (%)', 0)
    tss_angatuba_dia = volume_angatuba * conc_angatuba  # t_ss/dia

    return (total_tss / dias) + tss_angatuba_dia

## Cálculo ORT — Evaporação
# Fórmula:
#   TSS_fibra  = (-SOLIDO-SECO_fibra) × consumo_fibra   [t/ano, geração positiva]
#   TSS_total  = Σ TSS_fibra
#   Volume_LP  = Σ_fibras [ TSS_fibra / Conc_Digestor_fibra ]   [m³/ano de licor]
#   Evaporação = (1/24) × (Volume_LP - TSS_total / Conc_Saida_Evap)   [t_H2O/h]
#
# Mapeamento fibra → parâmetro de concentração do digestor (lidos do Excel):
#   CKB-FC → 'Concentração Licor Preto gerado no digestor 1 (%)'
#   CKB-FL → 'Concentração Licor Preto gerado no digestor 2 (%)'
#   CKN-FC → 'Concentração Licor Preto gerado no digestor 3 (%)'
#   CKN-FL → 'Concentração Licor Preto gerado no digestor 4 (%)'
#   BCTMP  → 'Concentração Licor Preto gerado na BCTMP (%)'
# Concentração na saída do evaporador: 'Concentração Licor Preto na Saída Evap1'

def calc_evaporacao_ORT(model):
    mapa_conc_digestor = {
        'CKB-FC': 'Concentração Licor Preto gerado no digestor 1 (%)',
        'CKB-FL': 'Concentração Licor Preto gerado no digestor 2 (%)',
        'CKN-FC': 'Concentração Licor Preto gerado no digestor 3 (%)',
        'CKN-FL': 'Concentração Licor Preto gerado no digestor 4 (%)',
        'BCTMP':  'Concentração Licor Preto gerado na BCTMP (%)',
    }

    conc_saida_evap = dict_param_add_ORT.get('Concentração Licor Preto na Saída Evap1', 1)

    volume_lp = 0
    tss_total  = 0

    for fibra, chave_conc in mapa_conc_digestor.items():
        # Reutiliza calc_tss_fibra_ORT — restringe a MC25 e MC26
        tss_fibra = calc_tss_fibra_ORT(model, fibra)
        conc_digestor = dict_param_add_ORT.get(chave_conc, 1)
        volume_lp += tss_fibra / conc_digestor
        tss_total  += tss_fibra

    # Contribuição de Angatuba — licor preto externo recebido diariamente
    # O volume já é dado diretamente (m³/dia); os sólidos = volume × concentração
    # Ambos são convertidos para base anual para consistência com as fibras
    dias = dict_dias_operacao.get('CDR', 365)
    volume_angatuba  = dict_param_add_ORT.get('Volume recebido de Licor Preto de Angatuba (m³/d)', 0)
    conc_angatuba    = dict_param_add_ORT.get('Concentração Licor Preto recebido de Angatuba (%)', 0)
    tss_angatuba_dia = volume_angatuba * conc_angatuba          # t_ss/dia
    volume_lp += volume_angatuba * dias                          # m³/ano
    tss_total  += tss_angatuba_dia * dias                        # t_ss/ano

    # (1/24) converte de volume/dia para volume/hora (unidade do limite: t_H2O/h)
    #return (1 / dias * 24) * (volume_lp - tss_total / conc_saida_evap)
    return (1 / (dias * 24)) * (volume_lp - tss_total / conc_saida_evap)

#MODEL

model = pyo.ConcreteModel()

#SETS

model.maquinas = pyo.Set(initialize={1,6,7,9,27,28,25,26,12,13,16,23})  ###Realmente utilizada
model.produtos = pyo.Set(initialize=lista_produtos)   ###Realmente utilizada
model.mercados = pyo.Set(initialize={'ME','MI','Transferência'}) ###Realmente utilizada
model.centros = pyo.Set(initialize={'MA','ORT'})
model.fibras = pyo.Set(initialize=todas_fibras)
model.tipos_cavaco = pyo.Set(initialize=['CAV-EUCALIPTO', 'CAV-PINUS'])
model.fibras_MA = pyo.Set(initialize=fibras_MA)
model.tipos_fibra_especificos_MA = pyo.Set(initialize=tipos_fibra_especificos_MA)
model.areas_balanco_MA = pyo.Set(initialize=areas_balanco_MA)
model.madeira_tipos = pyo.Set(initialize=lista_madeira)
model.nomes_parametros_balanco_MA = pyo.Set(initialize=nomes_parametros_balanco_MA)
model.fibras_ORT = pyo.Set(initialize=lista_fibras_ORT)
model.nomes_parametros_balanco_ORT = pyo.Set(initialize=nomes_parametros_balanco_ORT)
model.nomes_areas_capacPlantas = pyo.Set(initialize=nomes_area_capacPlantas)
model.fibras_ort = pyo.Set(initialize=nomes_fibras_ort)
model.parametros_modelo = pyo.Set(initialize=lista_parametros)

#PARAMETERS:

#Parâmetros de Produtividade:
model.Produtividade_max = pyo.Param(model.maquinas,model.produtos,initialize=dict_produtividade,default=0.0)
model.Taxa_PERF = pyo.Param(model.maquinas,model.produtos,initialize=dict_taxa_performance,default=0.0)
model.Produtividade_Bruta = pyo.Param(model.maquinas,model.produtos,initialize=dict_produtividade_bruta)
model.Refugo_Ajustado = pyo.Param(model.maquinas, model.produtos,initialize=lambda m, maq, prod: dict_refugo_ajustado.get((maq, prod), 0.0))
model.Waste = pyo.Param(model.maquinas, model.produtos,initialize=lambda m, maq, prod: dict_waste.get((maq, prod), 0.0))
model.Total_Waste = pyo.Param(model.maquinas, model.produtos,initialize=lambda m, maq, prod: dict_total_waste.get((maq, prod), 0.0))

#Parâmetros de Total Waste:
model.IAC = pyo.Param(model.maquinas,model.produtos, initialize=lambda m,maq,prod:dict_IAC.get((maq,prod),1))
model.Refugo_MP = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Refugo_MP.get((maq, prod), 0.0))
model.Rep_Externo = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Rep_Externo.get((maq, prod), 0.0))
model.MR2 = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_MR2.get((maq, prod), 0.0))
model.Sala_Perdas = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Sala_Perdas.get((maq, prod), 0.0))
model.Cortadeira_Perda_Gramatura = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Cortadeira_Perda_Gramatura.get((maq, prod), 0.0))
model.Cortadeira_Perdas_Cortadeira = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Cortadeira_Perdas_Cortadeira.get((maq, prod), 0.0))
model.Estoque_Perdas = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Estoque_Perdas.get((maq, prod), 0.0))
model.Estoque_Perdas_Refugo = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_Estoque_Perdas_Refugo.get((maq, prod), 0.0))

#Parâmetros de Desclassificados:
model.Taxa_Desclassificacao = pyo.Param(model.maquinas,model.produtos,model.produtos,initialize=lambda m,maq,prod,prod2: dict_taxa_desclassificado.get((maq,prod,prod2),0.0))

#Parâmetros de Máquinas:
model.Tempo_Carga = pyo.Param(model.maquinas,initialize=lambda m,maq:dict_tempo_carga.get((maq),0.0))
model.Taxa_DISP = pyo.Param(model.maquinas,initialize=lambda m,maq:dict_taxa_DISP.get((maq),0.0))
model.Taxa_QUAL_MAQUINA = pyo.Param(model.maquinas,initialize=lambda m,maq:dict_taxa_QUAL_MAQUINA.get((maq),0.0))
model.Prod_bruta_max = pyo.Param(model.maquinas,initialize=lambda m,maq:dict_Prod_bruta_max.get((maq),1e9))

#Parâmetros de Produto por Máquina:

model.Prod_por_Maq = pyo.Param(model.maquinas, model.produtos, initialize=lambda m, maq, prod: dict_prod_por_MP.get((maq, prod), 0.0))

# Parâmetros de Lista de Materiais:

model.Consumo_Especifico = pyo.Param(model.centros,model.produtos,model.maquinas,model.fibras,initialize=get_consumo_especifico,default=0.0)
model.Consumo_Cavaco = pyo.Param(model.centros,model.fibras,model.tipos_cavaco,initialize=get_consumo_cavaco,default=0.0)

#Parâmetros de Balanço de Fábrica ORT:

# Parâmetros da seção - Produção de celulose e consumo de fibras:
model.consumo_fibras_ORT = pyo.Param(model.fibras_ORT,initialize=lambda m,fibra: dict_consumo_fibras_ORT.get((fibra),0))
# Parâmetros adicionais ORT - mapeado por nome de parâmetro
model.Param_Balanco_ORT = pyo.Param(model.nomes_parametros_balanco_ORT, initialize=lambda m,nome_param: dict_param_add_ORT.get((nome_param),0))
#Parâmetros Capacidade das Plantas:
model.emissario = pyo.Param(model.nomes_areas_capacPlantas, initialize=lambda m,msr:dict_emissario.get(msr,0))
#Parâmetros Capacidade_Max das Plantas:
model.capac_max = pyo.Param(model.nomes_areas_capacPlantas, initialize=lambda m,capacmax:dict_capacmax_ort.get(capacmax,0))
#Parâmetros Dias Operação das Plantas:
model.dias_op = pyo.Param(model.nomes_areas_capacPlantas, initialize=lambda m,dias:dict_dias_operacao.get(dias,0))
#Parâmetros Fibras e digestores:
model.Rendimento_ORT = pyo.Param(model.fibras_ort,initialize=lambda m, fibra: dict_rendimento_ort.get(fibra, 0.0))
model.Carga_Alcalina_ORT = pyo.Param(model.fibras_ort,initialize=lambda m, fibra: dict_carga_alcalina_ort.get(fibra, 0.0))

#Parâmetros de Custos:
model.custos = pyo.Param(model.produtos,model.maquinas,initialize=lambda m,produto,maquina: dict_custos.get((produto,maquina),0.0))

#Parâmetros de demanda:

model.demanda = pyo.Param(model.produtos,model.mercados, initialize=lambda m,produtos,mercados: dict_demanda.get((produtos,mercados),0.0))
model.precos = pyo.Param(model.produtos,model.mercados, initialize=lambda m,produtos,mercados: dict_preco.get((produtos,mercados),0.0))

#Parâmetros de Parâmetros do modelo:

model.param_modelo = pyo.Param(model.parametros_modelo,initialize=lambda m,parametro: dict_param_modelo.get(parametro,0.0))


#DECISION VARIABLES

# VARIÁVEIS DE DECISÃO

# VARIÁVEIS DE DECISÃO — estrutura correta

# Produção bruta: física na máquina, SEM mercado
model.producao_bruta = pyo.Var(model.produtos, model.maquinas, domain=pyo.NonNegativeReals)

# Produção líquida: pool físico por máquina, SEM mercado
model.producao_liquida = pyo.Var(model.produtos, model.maquinas, domain=pyo.NonNegativeReals)

# Produção vendável: COM mercado — aqui ocorre a alocação
model.producao_vendavel = pyo.Var(model.produtos, model.maquinas, model.mercados, domain=pyo.NonNegativeReals)

# Demanda não atendida: COM mercado
model.demanda_nao_atendida = pyo.Var(model.produtos, model.mercados, domain=pyo.NonNegativeReals)

# Produção adicional: SEM mercado
model.producao_adicional = pyo.Var(model.produtos, model.maquinas, domain=pyo.NonNegativeReals)

# Refugo externo importado de OTA (MP12,13) → pulper CP (ADt/h)
model.refugo_ext_ota_cp = pyo.Var(domain=pyo.NonNegativeReals)

def _refugo_ext_cp_total(model):
    return model.refugo_ext_ota_cp

# Capacidade ociosa: SEM mercado
model.capacidade_ociosa = pyo.Var(model.maquinas, domain=pyo.NonNegativeReals)

#Restrições

# Demanda — por mercado (igual ao que já tem)
def Constraint_demanda(model, p, mercado):
    return (sum(model.producao_vendavel[p, m, mercado] for m in model.maquinas)
            + model.demanda_nao_atendida[p, mercado] == model.demanda[p, mercado])
model.Constraint_demanda = pyo.Constraint(model.produtos, model.mercados, rule=Constraint_demanda)

# Produção vendável = pool líquido alocado por mercado — SEM mercado na líquida
def Constraint_prod_vendavel(model, p, m):
    taxa_waste = dict_waste.get((m, p), 0)
    return (sum(model.producao_vendavel[p, m, mercado] for mercado in model.mercados)
            == model.producao_liquida[p, m] * (1 - taxa_waste))
model.Constraint_prod_vendavel = pyo.Constraint(model.produtos, model.maquinas, rule=Constraint_prod_vendavel)

# Desclassificados — SEM mercado na bruta e adicional
def Constraint_desclassificados(model, p, m):
    taxa_refugo = dict_refugo_ajustado.get((m, p), 0)
    return (model.producao_liquida[p, m]
            + (1 - taxa_refugo) * (
                model.producao_adicional[p, m]
                - sum(model.producao_bruta[p_origem, m]
                      * dict_taxa_desclassificado.get((m, p_origem, p), 1 if p_origem == p else 0)
                      for p_origem in model.produtos)
            ) == 0)
model.Constraint_desclassificados = pyo.Constraint(model.produtos, model.maquinas, rule=Constraint_desclassificados)

# Produto por máquina — SEM mercado
def Constraint_maquinas(model, p, m):
    if dict_prod_por_MP.get((m, p), 0) == 0:
        return model.producao_bruta[p, m] == 0
    return pyo.Constraint.Skip
model.Constraint_maquinas = pyo.Constraint(model.produtos, model.maquinas, rule=Constraint_maquinas)

# Tempo disponível — SEM mercado
def Constraint_tempo_producao(model, m):
    tempo_usado = sum(
        model.producao_bruta[p, m] / dict_produtividade_bruta.get((m, p), 1)
        for p in model.produtos
        if dict_produtividade_bruta.get((m, p), 0) > 0
    )
    return tempo_usado + model.capacidade_ociosa[m] == model.Tempo_Carga[m] * model.Taxa_DISP[m]
model.Constraint_tempo_producao = pyo.Constraint(model.maquinas, rule=Constraint_tempo_producao)

# Produção máxima — SEM mercado
def Constraint_producao_maxima(model, m):
    return (sum(model.producao_bruta[p, m] for p in model.produtos)
            <= model.Prod_bruta_max[m])
model.Constraint_producao_maxima = pyo.Constraint(model.maquinas, rule=Constraint_producao_maxima)

# Balanço MA — Sólidos com cinzas
def Constraint_tss(model):
    return calc_tss_total(model, 'MA') <= dict_capacidade_MSR.get('Sólidos c/ cinzas', 0)
model.Constraint_tss = pyo.Constraint(rule=Constraint_tss)

# Balanço MA — DISPER-MPC (geração não pode superar consumo)
def Constraint_disper(model):
    return calc_disper(model) <= 0
model.Constraint_disper = pyo.Constraint(rule=Constraint_disper)

# Balanço MA — Digestores
MAPA_DIGESTOR_MA = {'esco': 'Esco', 'kamyr': 'Kamyr', 'ctmp': 'CTMP'}

def Constraint_digestor_MA(model, digestor):
    return calc_producao_digestor_MA(model, digestor) <= dict_capacidade_MSR.get(MAPA_DIGESTOR_MA[digestor], 1e9)
model.Constraint_digestor_MA = pyo.Constraint(['esco', 'kamyr', 'ctmp'], rule=Constraint_digestor_MA)

# Balanço MA — Evaporação
def Constraint_evaporacao(model):
    return calc_evaporacao(model) <= dict_capacidade_MSR.get('Evaporação', 0)
model.Constraint_evaporacao = pyo.Constraint(rule=Constraint_evaporacao)

# Balanço MA — Licor Verde
def Constraint_licor_verde(model):
    return calc_licor_verde(model) <= dict_capacidade_MSR.get('Licor verde', 0)
model.Constraint_licor_verde = pyo.Constraint(rule=Constraint_licor_verde)

# Balanço MA — PMAD
def Constraint_pmad(model):
    return calc_pmad(model) <= dict_capacidade_MSR.get('PMAD', 0)
model.Constraint_pmad = pyo.Constraint(rule=Constraint_pmad)

# Balanço ORT — Caustificação
def Constraint_caustificacao(model):
    return calc_caustificacao(model) <= dict_emissario.get('Caustificação', 0)
model.Constraint_caustificacao = pyo.Constraint(rule=Constraint_caustificacao)

# Balanço ORT — Captação
def Constraint_captacao(model):
    return calc_captacao(model) <= dict_emissario.get('Outorga Captação', 0)
model.Constraint_captacao = pyo.Constraint(rule=Constraint_captacao)

# Balanço ORT — Emissário
def Constraint_emissario(model):
    return calc_emissario(model) <= dict_emissario.get('Outorga Emissario', 0)
model.Constraint_emissario = pyo.Constraint(rule=Constraint_emissario)

# Balanço ORT — Digestores
def Constraint_digestor_ORT(model, digestor):
    return calc_producao_digestor_ORT(model, digestor) <= dict_emissario.get(digestor, 1e9)
model.Constraint_digestor_ORT = pyo.Constraint(list(lista_fibras_ORT), rule=Constraint_digestor_ORT)

# Balanço ORT — CDR (Caldeira de Recuperação)
def Constraint_cdr(model):
    return calc_cdr(model) <= dict_emissario.get('CDR', 1e9)
model.Constraint_cdr = pyo.Constraint(rule=Constraint_cdr)

# Balanço ORT — Evaporação
def Constraint_evaporacao_ORT(model):
    return calc_evaporacao_ORT(model) <= dict_emissario.get('Evaporação', 1e9)
model.Constraint_evaporacao_ORT = pyo.Constraint(rule=Constraint_evaporacao_ORT)

# Balanço OTA — Lavagem (L2 + L4)
def Constraint_lavagem_OTA(model):
    return calc_lavagem_OTA(model) <= _lim_lavagem_OTA
model.Constraint_lavagem_OTA = pyo.Constraint(rule=Constraint_lavagem_OTA)

# Balanço OTA — Cozimento
def Constraint_cozimento_OTA(model):
    return calc_cozimento_OTA(model) <= _lim_cozimento_OTA
model.Constraint_cozimento_OTA = pyo.Constraint(rule=Constraint_cozimento_OTA)

# Balanço OTA — CDR4
def Constraint_cdr_OTA(model):
    return calc_cdr_OTA(model) <= _lim_cdr4_OTA
model.Constraint_cdr_OTA = pyo.Constraint(rule=Constraint_cdr_OTA)

# Balanço OTA — Caustificação
def Constraint_caustificacao_OTA(model):
    return calc_caustificacao_OTA(model) <= _lim_caustificacao_OTA
model.Constraint_caustificacao_OTA = pyo.Constraint(rule=Constraint_caustificacao_OTA)

# Balanço OTA — Evaporação (Pré Evap 1+2 + Evap 2+3)
def Constraint_evaporacao_OTA(model):
    return calc_evaporacao_OTA(model) <= _lim_evaporacao_OTA
model.Constraint_evaporacao_OTA = pyo.Constraint(rule=Constraint_evaporacao_OTA)

# ── Balanço CP ──────────────────────────────────────────────────────────────

def Constraint_cozimento_CP(model):
    return calc_cozimento_CP(model) <= _lim_cozimento_CP
model.Constraint_cozimento_CP = pyo.Constraint(rule=Constraint_cozimento_CP)

def Constraint_lavagem1_CP(model):
    return calc_lavagem1_CP(model) <= _lim_lavagem1_CP
model.Constraint_lavagem1_CP = pyo.Constraint(rule=Constraint_lavagem1_CP)

def Constraint_lavagem2_CP(model):
    return calc_lavagem2_CP(model) <= _lim_lavagem2_CP
model.Constraint_lavagem2_CP = pyo.Constraint(rule=Constraint_lavagem2_CP)

def Constraint_deslignificacao_CP(model):
    return calc_lavagem2_CP(model) <= _lim_deslignif_CP
model.Constraint_deslignificacao_CP = pyo.Constraint(rule=Constraint_deslignificacao_CP)

def Constraint_pulper_CP(model):
    return calc_pulper_CP(model) <= _lim_pulper_CP
model.Constraint_pulper_CP = pyo.Constraint(rule=Constraint_pulper_CP)

def Constraint_cr2_CP(model):
    return calc_cr2_CP(model) <= _lim_cr2_CP
model.Constraint_cr2_CP = pyo.Constraint(rule=Constraint_cr2_CP)

# Refugo externo OTA → CP: limitado pela produção real de MP12+MP13
def Constraint_refugo_ext_ota_cp(model):
    return model.refugo_ext_ota_cp <= _calc_refugo_ext_disponivel_adt_h(model, [12, 13])
model.Constraint_refugo_ext_ota_cp = pyo.Constraint(rule=Constraint_refugo_ext_ota_cp)

# Lavagem1 não pode ser negativa (importar mais fibra do que a demanda CKN seria fisicamente inviável)
def Constraint_lavagem1_CP_nonneg(model):
    return calc_lavagem1_CP(model) >= 0  # impede refugo total > demanda CKN
model.Constraint_lavagem1_CP_nonneg = pyo.Constraint(rule=Constraint_lavagem1_CP_nonneg)

def Constraint_caustificacao_CP(model):
    return calc_caustificacao_CP(model) <= _lim_caustificacao_CP
model.Constraint_caustificacao_CP = pyo.Constraint(rule=Constraint_caustificacao_CP)

def Constraint_evaporacao_CP(model):
    return calc_evaporacao_CP(model) <= _lim_evaporacao_CP
model.Constraint_evaporacao_CP = pyo.Constraint(rule=Constraint_evaporacao_CP)

# Função Objetivo — Max. Margem (Receita - Custo Variável)
def obj_max_margem(model):
    receita = sum(
        model.producao_vendavel[p, m, merc] * dict_preco.get((p, merc), 0)
        for p in model.produtos
        for m in model.maquinas
        for merc in model.mercados
    )
    custo = sum(
        model.producao_bruta[p, m] * (
                dict_custos.get((p, m), 0)
                + flag_remuneracao_celulose * (
                        get_consumo_especifico(model, 'ORT', p, m, 'CKB-FC') * (
                            preco_venda_fibra_curta - custo_variavel_fibra_curta)
                        + get_consumo_especifico(model, 'ORT', p, m, 'CKB-FL') * (
                                    preco_venda_fibra_longa - custo_variavel_fibra_longa)
                )
        )
        for p in model.produtos
        for m in model.maquinas
    )
    penalidade_ociosidade = sum(
        model.capacidade_ociosa[m] * dict_penalidade_ociosidade.get(m, 0)
        for m in model.maquinas
    )
    return receita - custo - penalidade_ociosidade
model.obj = pyo.Objective(rule=obj_max_margem, sense=pyo.maximize)

#"""
# Solver
solver = pyo.SolverFactory('glpk')
results = solver.solve(model, tee=True)

# Verificação do status
if results.solver.termination_condition == pyo.TerminationCondition.optimal:
    print("\n✓ Solução ótima encontrada!")

    print("\n=== PRODUÇÃO BRUTA (> 0) ===")
    for p in model.produtos:
        for m in model.maquinas:
            val = pyo.value(model.producao_bruta[p, m])
            if val and val > 0:
                print(f"  {p} | MP{m}: {val:.1f} t")

    print("\n=== PRODUÇÃO VENDÁVEL (> 0) ===")
    for p in model.produtos:
        for m in model.maquinas:
            for merc in model.mercados:
                val = pyo.value(model.producao_vendavel[p, m, merc])
                if val and val > 0:
                    print(f"  {p} | MP{m} | {merc}: {val:.1f} t")

    print("\n=== DEMANDA NÃO ATENDIDA (> 0) ===")
    for p in model.produtos:
        for merc in model.mercados:
            val = pyo.value(model.demanda_nao_atendida[p, merc])
            if val and val > 0:
                print(f"  {p} | {merc}: {val:.1f} t")

    print("\n=== CAPACIDADE OCIOSA (> 0) ===")
    for m in model.maquinas:
        val = pyo.value(model.capacidade_ociosa[m])
        if val and val > 0:
            print(f"  MP{m}: {val:.1f} h")

    print("=== VALIDAÇÃO DAS RESTRIÇÕES ===")
    print("\nTempo usado vs disponível (h):")
    for m in model.maquinas:
        tempo_usado = sum(
            pyo.value(model.producao_bruta[p, m]) / dict_produtividade_bruta.get((m, p), 1)
            for p in model.produtos
            if dict_produtividade_bruta.get((m, p), 0) > 0
        )
        tempo_disp = dict_tempo_carga.get(m, 0) * dict_taxa_DISP.get(m, 1)
        print(f"  MP{m}: usado={tempo_usado:.1f} | disponível={tempo_disp:.1f} | ocioso={tempo_disp - tempo_usado:.1f}")

    print("\nDemanda atendida vs total:")
    total_demanda = 0
    total_atendida = 0
    total_nao_atendida = 0

    for (p, merc), qtd in dict_demanda.items():
        if qtd == 0:
            continue
        total_demanda += qtd
        atendida = sum(pyo.value(model.producao_vendavel[p, m, merc]) for m in model.maquinas)
        nao_atendida = pyo.value(model.demanda_nao_atendida[p, merc])
        total_atendida += atendida
        total_nao_atendida += nao_atendida

    print(f"  Total demanda:      {total_demanda:,.1f} t")
    print(f"  Total atendida:     {total_atendida:,.1f} t")
    print(f"  Total não atendida: {total_nao_atendida:,.1f} t")
    print(f"  Cobertura:          {100 * total_atendida / total_demanda:.1f}%")
#"""

########## Código

print("\n" + "="*110)
print("COMPARAÇÃO DEMANDA vs PRODUÇÃO VENDÁVEL POR PRODUTO E MERCADO")
print("="*110)
print(f"{'Produto':<20} {'Mercado':<15} {'Demanda':>12} {'Atendida':>12} {'Diferença':>12} {'Cobertura':>10}  {'Máquinas utilizadas'}")
print("-"*110)

linhas = []
for (p, merc), qtd in dict_demanda.items():
    if qtd == 0:
        continue
    atendida = sum(pyo.value(model.producao_vendavel[p, m, merc]) for m in model.maquinas)
    diferenca = qtd - atendida
    cobertura = 100 * atendida / qtd if qtd > 0 else 100

    # Máquinas que efetivamente produziram para esse produto/mercado
    maquinas_usadas = [
        f"MP{m}={pyo.value(model.producao_vendavel[p, m, merc]):,.0f}t"
        for m in model.maquinas
        if pyo.value(model.producao_vendavel[p, m, merc]) > 0.1
    ]
    maquinas_str = ", ".join(maquinas_usadas) if maquinas_usadas else "—"

    linhas.append((p, merc, qtd, atendida, diferenca, cobertura, maquinas_str))

linhas.sort(key=lambda x: -x[4])

total_dem = 0
total_at = 0
total_dif = 0

for p, merc, qtd, atendida, diferenca, cobertura, maquinas_str in linhas:
    total_dem += qtd
    total_at += atendida
    total_dif += diferenca
    flag = " ← sem máquina" if not any(dict_prod_por_MP.get((m, p), 0) == 1 for m in [1,6,7,9,27,28]) else ""
    print(f"  {p:<18} {merc:<15} {qtd:>12,.1f} {atendida:>12,.1f} {diferenca:>12,.1f} {cobertura:>9.1f}%  {maquinas_str}{flag}")

print("-"*110)
print(f"  {'TOTAL':<18} {'':15} {total_dem:>12,.1f} {total_at:>12,.1f} {total_dif:>12,.1f} {100*total_at/total_dem:>9.1f}%")

print("\n" + "="*70)
print("UTILIZAÇÃO DAS RESTRIÇÕES DE BALANÇO DE FÁBRICA")
print("="*70)

print(f"\n--- FÁBRICA MA ---")

evap = pyo.value(calc_evaporacao(model))
evap_lim = dict_capacidade_MSR.get('Evaporação', 0)
print(f"  Evaporação:        {evap:>10.2f} / {evap_lim:>10.2f}  ({100*evap/evap_lim:.1f}%)")

lv = pyo.value(calc_licor_verde(model))
lv_lim = dict_capacidade_MSR.get('Licor verde', 0)
print(f"  Licor verde:       {lv:>10.2f} / {lv_lim:>10.2f}  ({100*lv/lv_lim:.1f}%)")

pmad = pyo.value(calc_pmad(model))
pmad_lim = dict_capacidade_MSR.get('PMAD', 0)
print(f"  PMAD:              {pmad:>10.2f} / {pmad_lim:>10.2f}  ({100*pmad/pmad_lim:.1f}%)")

tss = pyo.value(calc_tss_total(model, 'MA'))
tss_lim = dict_capacidade_MSR.get('Sólidos c/ cinzas', 0)
print(f"  Sólidos c/ cinzas: {tss:>10.2f} / {tss_lim:>10.2f}  ({100*tss/tss_lim:.1f}%)")

disper = pyo.value(calc_disper(model))
print(f"  DISPER-MPC:        {disper:>10.2f} / {0:>10.2f}  (deve ser ≤ 0)")

print(f"\n  Digestores MA:")
for digestor, chave_msr in [('esco', 'Esco'), ('kamyr', 'Kamyr'), ('ctmp', 'CTMP')]:
    prod = pyo.value(calc_producao_digestor_MA(model, digestor))
    lim = dict_capacidade_MSR.get(chave_msr, 0)
    pct = 100*prod/lim if lim > 0 else 0
    print(f"    {digestor:<8}: {prod:>10.2f} / {lim:>10.2f}  ({pct:.1f}%)")

print(f"\n--- FÁBRICA ORT ---")

caust = pyo.value(calc_caustificacao(model))
caust_lim = dict_emissario.get('Caustificação', 0)
print(f"  Caustificação:     {caust:>10.2f} / {caust_lim:>10.2f}  ({100*caust/caust_lim:.1f}%)")

capt = pyo.value(calc_captacao(model))
capt_lim = dict_emissario.get('Outorga Captação', 0)
print(f"  Outorga Captação:  {capt:>10.2f} / {capt_lim:>10.2f}  ({100*capt/capt_lim:.1f}%)")

emis = pyo.value(calc_emissario(model))
emis_lim = dict_emissario.get('Outorga Emissario', 0)
print(f"  Outorga Emissário: {emis:>10.2f} / {emis_lim:>10.2f}  ({100*emis/emis_lim:.1f}%)")

print(f"\n  Digestores ORT:")
for fibra in lista_fibras_ORT:
    prod = pyo.value(calc_producao_digestor_ORT(model, fibra))
    lim = dict_emissario.get(fibra, 0)
    pct = 100*prod/lim if lim > 0 else 0
    print(f"    {fibra:<10}: {prod:>10.2f} / {lim:>10.2f}  ({pct:.1f}%)")

cdr_val = pyo.value(calc_cdr(model))
cdr_lim = dict_emissario.get('CDR', 0)
print(f"\n  CDR (Caldeira de Recuperação):")
print(f"    CDR: {cdr_val:>10.2f} / {cdr_lim:>10.2f}  ({100*cdr_val/cdr_lim:.1f}%)")

evap_ort_val = pyo.value(calc_evaporacao_ORT(model))
evap_ort_lim = dict_emissario.get('Evaporação', 0)
print(f"\n  Evaporação ORT:")
print(f"    Evap: {evap_ort_val:>10.2f} / {evap_ort_lim:>10.2f}  ({100*evap_ort_val/evap_ort_lim:.1f}%)")

print(f"\n--- FÁBRICA OTA ---")

lav_ota = pyo.value(calc_lavagem_OTA(model))
print(f"  Lavagem (L2+L4):   {lav_ota:>10.2f} / {_lim_lavagem_OTA:>10.2f} ADt/h  ({100*lav_ota/_lim_lavagem_OTA:.1f}%)")

coz_ota = pyo.value(calc_cozimento_OTA(model))
print(f"  Cozimento:         {coz_ota:>10.2f} / {_lim_cozimento_OTA:>10.2f} ADt/h  ({100*coz_ota/_lim_cozimento_OTA:.1f}%)")

cdr_ota = pyo.value(calc_cdr_OTA(model))
print(f"  CDR4:              {cdr_ota:>10.2f} / {_lim_cdr4_OTA:>10.2f} tSS/h  ({100*cdr_ota/_lim_cdr4_OTA:.1f}%)")

caust_ota = pyo.value(calc_caustificacao_OTA(model))
print(f"  Caustificação:     {caust_ota:>10.2f} / {_lim_caustificacao_OTA:>10.2f} m³/h   ({100*caust_ota/_lim_caustificacao_OTA:.1f}%)")

evap_ota = pyo.value(calc_evaporacao_OTA(model))
print(f"  Evaporação:        {evap_ota:>10.2f} / {_lim_evaporacao_OTA:>10.2f} tAE/h  ({100*evap_ota/_lim_evaporacao_OTA:.1f}%)")

print(f"\n--- FÁBRICA CP ---")

coz_cp   = pyo.value(calc_cozimento_CP(model))
lav1_cp  = pyo.value(calc_lavagem1_CP(model))
lav2_cp  = pyo.value(calc_lavagem2_CP(model))
pulp_cp  = pyo.value(calc_pulper_CP(model))
cr2_cp   = pyo.value(calc_cr2_CP(model))
caust_cp = pyo.value(calc_caustificacao_CP(model))
evap_cp  = pyo.value(calc_evaporacao_CP(model))

print(f"  Cozimento:         {coz_cp:>10.2f} / {_lim_cozimento_CP:>10.2f} ADt/h    ({100*coz_cp/_lim_cozimento_CP:.1f}%)")
print(f"  Lavagem 1:         {lav1_cp:>10.2f} / {_lim_lavagem1_CP:>10.2f} ADt/h    ({100*lav1_cp/_lim_lavagem1_CP:.1f}%)")
print(f"  Lavagem 2:         {lav2_cp:>10.2f} / {_lim_lavagem2_CP:>10.2f} ADt/h    ({100*lav2_cp/_lim_lavagem2_CP:.1f}%)")
print(f"  Deslignificação:   {lav2_cp:>10.2f} / {_lim_deslignif_CP:>10.2f} ADt/h    ({100*lav2_cp/_lim_deslignif_CP:.1f}%)")
print(f"  Pulper IP40:       {pulp_cp:>10.2f} / {_lim_pulper_CP:>10.2f} ADt/h    ({100*pulp_cp/_lim_pulper_CP:.1f}%)")
print(f"  CR2:               {cr2_cp:>10.2f} / {_lim_cr2_CP:>10.2f} tss/d    ({100*cr2_cp/_lim_cr2_CP:.1f}%)")
print(f"  Caustificação:     {caust_cp:>10.2f} / {_lim_caustificacao_CP:>10.2f} m³ Lb/d ({100*caust_cp/_lim_caustificacao_CP:.1f}%)")
print(f"  Evaporação (vapor):{evap_cp:>10.2f} / {_lim_evaporacao_CP:>10.2f} tv/h     ({100*evap_cp/_lim_evaporacao_CP:.1f}%)")

_repulp_ckn_adt = pyo.value(_calc_waste_repulp_adt_h(model, _PREFIXOS_CKN_CP, _MAQUINAS_CP)) * _DIAS_CP * 24
_repulp_ckd_adt = pyo.value(_calc_waste_repulp_adt_h(model, _PREFIXOS_CKD_CP, _MAQUINAS_CP)) * _DIAS_CP * 24
_repulp_ckb_adt = pyo.value(_calc_waste_repulp_adt_h(model, _PREFIXOS_CKB_CP, _MAQUINAS_CP)) * _DIAS_CP * 24
print(f"\n  Waste interno reaproveitado via pulper (η = {_ETA_REPULP_CP:.0%}):")
print(f"    CKN: {_repulp_ckn_adt:>10.1f} ADt/ano  → alivia Cozimento + Lavagem 1 + CR2")
print(f"    CKD: {_repulp_ckd_adt:>10.1f} ADt/ano  → alivia Cozimento + Lavagem 2 + CR2")
print(f"    CKB: {_repulp_ckb_adt:>10.1f} ADt/ano  → carga adicional somente no Pulper")
print(f"    Total interno: {_repulp_ckn_adt + _repulp_ckd_adt + _repulp_ckb_adt:>8.1f} ADt/ano")
_K = _DIAS_CP * 24
_cp_ckn  = pyo.value(_calc_waste_repulp_adt_h(model, _PREFIXOS_CKN_CP, _MAQUINAS_CP)) * _K
_cp_ckd  = pyo.value(_calc_waste_repulp_adt_h(model, _PREFIXOS_CKD_CP, _MAQUINAS_CP)) * _K
_cp_ckb  = pyo.value(_calc_waste_repulp_adt_h(model, _PREFIXOS_CKB_CP, _MAQUINAS_CP)) * _K
_cp_tot  = _cp_ckn + _cp_ckd + _cp_ckb
_ota_uso = pyo.value(model.refugo_ext_ota_cp) * _K
_ota_dsp = pyo.value(_calc_refugo_ext_disponivel_adt_h(model, [12, 13])) * _K
def _pct(u, d): return f"{u/d*100:.1f}%" if d > 0 else "—"
print(f"\n  {'='*62}")
print(f"  REAPROVEITAMENTO DE REFUGO NO PULPER CP (η = {_ETA_REPULP_CP:.0%} interno / {_ETA_REPULP_EXT_CP:.0%} externo)")
print(f"  {'='*62}")
print(f"  {'Origem':<22} {'ADt/ano':>10}  Observação")
print(f"  {'-'*62}")
print(f"  {'CP — CKN (própria)':<22} {_cp_ckn:>10,.1f}  alivia Lavagem 1 + CR2")
print(f"  {'CP — CKD (própria)':<22} {_cp_ckd:>10,.1f}  alivia Lavagem 2 + CR2")
print(f"  {'CP — CKB (própria)':<22} {_cp_ckb:>10,.1f}  carga no Pulper apenas")
print(f"  {'-'*62}")
print(f"  {'CP total':<22} {_cp_tot:>10,.1f}")
print(f"  {'-'*62}")
print(f"  {'OTA — MP12+MP13':<22} {_ota_uso:>10,.1f}  alivia Cozimento + Lavagem 1 + CR2")
print(f"  {'OTA disponível (máx)':<22} {_ota_dsp:>10,.1f}  ({_pct(_ota_uso, _ota_dsp)} da oferta OTA utilizado)")
print(f"  {'='*62}")
print(f"  {'Total reaproveitado':<22} {_cp_tot + _ota_uso:>10,.1f}  ADt/ano")

_total_gerado    = _ota_dsp + _cp_tot
_total_utilizado = _ota_uso + _cp_tot
print(f"\n  REFUGO GERADO vs UTILIZADO NO PULPER CP")
print(f"  {'-'*62}")
print(f"  {'Unidade':<22} {'Gerado (ADt/ano)':>16}  {'-> Pulper CP':>12}  {'Aproveit.':>9}")
print(f"  {'-'*62}")
print(f"  {'OTA (MP12+MP13)':<22} {_ota_dsp:>16,.1f}  {_ota_uso:>12,.1f}  {_pct(_ota_uso, _ota_dsp):>9}")
print(f"  {'CP  (MP16+MP23)':<22} {_cp_tot:>16,.1f}  {_cp_tot:>12,.1f}  {'100.0%':>9}")
print(f"  {'-'*62}")
print(f"  {'Total':<22} {_total_gerado:>16,.1f}  {_total_utilizado:>12,.1f}  {_pct(_total_utilizado, _total_gerado):>9}")

print("\n" + "="*70)
print("PRODUÇÃO BRUTA POR PRODUTO — MP12 e MP13 (Fábrica OC)")
print("="*70)
for _m_oc in [12, 13]:
    print(f"\n  MP{_m_oc}:")
    print(f"  {'Produto':<30} {'Prod. Bruta (t)':>15}")
    print(f"  {'-'*47}")
    _total_oc = 0.0
    for _p_oc in sorted(model.produtos):
        _v_oc = pyo.value(model.producao_bruta[_p_oc, _m_oc]) or 0
        if _v_oc > 0.1:
            print(f"  {_p_oc:<30} {_v_oc:>15,.1f}")
            _total_oc += _v_oc
    print(f"  {'-'*47}")
    print(f"  {'TOTAL':<30} {_total_oc:>15,.1f}")

import json

# ── Diagnóstico IA ─────────────────────────────────────────────────────────

_diag_r = {}

for _m in model.maquinas:
    _t_us = sum(
        (pyo.value(model.producao_bruta[_p, _m]) or 0) / dict_produtividade_bruta.get((_m, _p), 1)
        for _p in model.produtos if dict_produtividade_bruta.get((_m, _p), 0) > 0
    )
    _t_lim = dict_tempo_carga.get(_m, 0) * dict_taxa_DISP.get(_m, 1)
    _diag_r[f'Tempo MP{_m}'] = {'nome': f'Tempo MP{_m}', 'usado': round(_t_us, 1),
                                  'limite': round(_t_lim, 1), 'unidade': 'h', 'tipo': 'maquina'}

for _nm, _vl, _lm, _un in [
    ('Evaporação',     pyo.value(calc_evaporacao(model)),                  dict_capacidade_MSR.get('Evaporação', 0),       'm³/h'),
    ('Licor Verde',    pyo.value(calc_licor_verde(model)),                 dict_capacidade_MSR.get('Licor verde', 0),      'm³/d'),
    ('PMAD',           pyo.value(calc_pmad(model)),                        dict_capacidade_MSR.get('PMAD', 0),             't/d'),
    ('Sólidos/Cinzas', pyo.value(calc_tss_total(model, 'MA')),             dict_capacidade_MSR.get('Sólidos c/ cinzas', 0),'t/d'),
    ('Digestor Esco',  pyo.value(calc_producao_digestor_MA(model, 'esco')),  dict_capacidade_MSR.get('Esco', 0),           't/d'),
    ('Digestor Kamyr', pyo.value(calc_producao_digestor_MA(model, 'kamyr')), dict_capacidade_MSR.get('Kamyr', 0),         't/d'),
    ('Digestor CTMP',  pyo.value(calc_producao_digestor_MA(model, 'ctmp')),  dict_capacidade_MSR.get('CTMP', 0),          't/d'),
]:
    _diag_r[_nm] = {'nome': _nm, 'usado': round(_vl or 0, 2), 'limite': round(_lm, 2), 'unidade': _un, 'tipo': 'MA'}

for _nm, _vl, _lm, _un in [
    ('Caustificação',    pyo.value(calc_caustificacao(model)),    dict_emissario.get('Caustificação', 0),     'm³/d'),
    ('Outorga Captação', pyo.value(calc_captacao(model)),         dict_emissario.get('Outorga Captação', 0),  'm³/h'),
    ('Outorga Emissário',pyo.value(calc_emissario(model)),        dict_emissario.get('Outorga Emissario', 0), 'm³/h'),
    ('CDR',              pyo.value(calc_cdr(model)),              dict_emissario.get('CDR', 0),               'tss/d'),
    ('Evaporação ORT',   pyo.value(calc_evaporacao_ORT(model)),   dict_emissario.get('Evaporação', 0),        't_H2O/h'),
]:
    _diag_r[_nm] = {'nome': _nm, 'usado': round(_vl or 0, 2), 'limite': round(_lm, 2), 'unidade': _un, 'tipo': 'ORT'}

for _f in lista_fibras_ORT:
    _vl2 = pyo.value(calc_producao_digestor_ORT(model, _f)) or 0
    _lm2 = dict_emissario.get(_f, 0)
    _diag_r[_f] = {'nome': _f, 'usado': round(_vl2, 2), 'limite': round(_lm2, 2), 'unidade': 't/d', 'tipo': 'ORT'}

for _nm_ota, _vl_ota, _lm_ota, _un_ota in [
    ('Lavagem OTA',      pyo.value(calc_lavagem_OTA(model)),      _lim_lavagem_OTA,      'ADt/h'),
    ('Cozimento OTA',    pyo.value(calc_cozimento_OTA(model)),    _lim_cozimento_OTA,    'ADt/h'),
    ('CDR OTA',          pyo.value(calc_cdr_OTA(model)),          _lim_cdr4_OTA,         'tSS/h'),
    ('Caustificação OTA',pyo.value(calc_caustificacao_OTA(model)),_lim_caustificacao_OTA,'m³/h'),
    ('Evaporação OTA',   pyo.value(calc_evaporacao_OTA(model)),   _lim_evaporacao_OTA,   'tAE/h'),
]:
    _diag_r[_nm_ota] = {'nome': _nm_ota, 'usado': round(_vl_ota or 0, 2),
                         'limite': round(_lm_ota, 2), 'unidade': _un_ota, 'tipo': 'OTA'}

for _nm_cp2, _vl_cp2, _lm_cp2, _un_cp2 in [
    ('Cozimento CP',       pyo.value(calc_cozimento_CP(model)),     _lim_cozimento_CP,     'ADt/h'),
    ('Lavagem 1 CP',       pyo.value(calc_lavagem1_CP(model)),      _lim_lavagem1_CP,      'ADt/h'),
    ('Lavagem 2 CP',       pyo.value(calc_lavagem2_CP(model)),      _lim_lavagem2_CP,      'ADt/h'),
    ('Deslignificação CP', pyo.value(calc_lavagem2_CP(model)),      _lim_deslignif_CP,     'ADt/h'),
    ('Pulper CP',          pyo.value(calc_pulper_CP(model)),        _lim_pulper_CP,        'ADt/h'),
    ('CR2 CP',             pyo.value(calc_cr2_CP(model)),           _lim_cr2_CP,           'tss/d'),
    ('Caustificação CP',   pyo.value(calc_caustificacao_CP(model)), _lim_caustificacao_CP, 'm³ Lb/d'),
    ('Evaporação CP',      pyo.value(calc_evaporacao_CP(model)),    _lim_evaporacao_CP,    'tv/h'),
]:
    _diag_r[_nm_cp2] = {
        'nome':    _nm_cp2,
        'usado':   round(_vl_cp2 or 0, 2),
        'limite':  round(_lm_cp2, 2),
        'unidade': _un_cp2,
        'tipo':    'CP'
    }

def _diag_cls(r):
    if r['limite'] <= 0: return 'N/A', 0.0
    ratio = r['usado'] / r['limite']
    if ratio > 1.001:   return 'VIOLADA', round(100 * ratio, 1)
    elif ratio >= 0.95: return 'CRÍTICA', round(100 * ratio, 1)
    elif ratio >= 0.80: return 'ATIVA',   round(100 * ratio, 1)
    else:               return 'FOLGADA', round(100 * ratio, 1)

restricoes_status = {}
for _k, _rv in _diag_r.items():
    _st, _pt = _diag_cls(_rv)
    restricoes_status[_k] = {**_rv, 'status': _st, 'percentual': _pt,
                              'folga': round(_rv['limite'] - _rv['usado'], 2)}

restricoes_criticas = {k: v for k, v in restricoes_status.items()
                       if v['status'] in ('CRÍTICA', 'VIOLADA')}

alocacao_produtos = []
for _p in sorted(model.produtos):
    _dem = sum(dict_demanda.get((_p, _mc), 0) for _mc in model.mercados)
    if _dem == 0: continue
    _prod = sum((pyo.value(model.producao_vendavel[_p, _m, _mc]) or 0)
                for _m in model.maquinas for _mc in model.mercados)
    _mq_us = [f'MP{_m}' for _m in model.maquinas
               if sum((pyo.value(model.producao_vendavel[_p, _m, _mc]) or 0)
                      for _mc in model.mercados) > 0.1]
    _mq_dp = [f'MP{_m}' for _m in model.maquinas if dict_prod_por_MP.get((_m, _p), 0) == 1]
    _gap_p = round(_dem - _prod, 1)
    _rec_p = sum((pyo.value(model.producao_vendavel[_p, _m, _mc]) or 0) * dict_preco.get((_p, _mc), 0)
                 for _m in model.maquinas for _mc in model.mercados)
    _cst_p = sum((pyo.value(model.producao_bruta[_p, _m]) or 0) * dict_custos.get((_p, _m), 0)
                 for _m in model.maquinas)
    alocacao_produtos.append({
        'produto': _p, 'demanda': round(_dem, 1), 'producao': round(_prod, 1), 'gap': _gap_p,
        'maquinas_usadas': _mq_us, 'maquinas_disponiveis': _mq_dp,
        'margem': round(_rec_p - _cst_p, 0),
        'motivo': ('Sem máquina habilitada' if not _mq_dp
                   else 'Atendida' if _gap_p <= 0.1
                   else 'Gap: capacidade insuficiente')
    })

sensibilidade_restricoes = {}
for _k, _rv in restricoes_status.items():
    if _rv['status'] not in ('CRÍTICA', 'ATIVA'): continue
    _top = []
    if _rv['tipo'] == 'maquina':
        _mn = int(_k.replace('Tempo MP', ''))
        _cs = [(_p, round((pyo.value(model.producao_bruta[_p, _mn]) or 0)
                           / dict_produtividade_bruta.get((_mn, _p), 1), 1))
               for _p in model.produtos
               if dict_produtividade_bruta.get((_mn, _p), 0) > 0
               and (pyo.value(model.producao_bruta[_p, _mn]) or 0) > 0.1]
        _top = [{'produto': t[0], 'h': t[1]} for t in sorted(_cs, key=lambda x: -x[1])[:5]]
    sensibilidade_restricoes[_k] = {
        'status': _rv['status'], 'percentual': _rv['percentual'],
        'folga': _rv['folga'], 'unidade': _rv['unidade'],
        'top_consumidores': _top,
        'interpretacao': (f"{_k}: {_rv['percentual']}% do limite "
                          f"({_rv['usado']} / {_rv['limite']} {_rv['unidade']}). "
                          f"Folga: {_rv['folga']} {_rv['unidade']}.")
    }

_oc_h = {_m: (pyo.value(model.capacidade_ociosa[_m]) or 0) for _m in model.maquinas}
_gap_lst = sorted([r for r in alocacao_produtos if r['gap'] > 0.1], key=lambda x: -x['gap'])
trocas_possiveis = []
for _rw in _gap_lst[:10]:
    _p = _rw['produto']
    _cap_mq = {}
    for _m in model.maquinas:
        if dict_prod_por_MP.get((_m, _p), 0) == 0: continue
        _pb = dict_produtividade_bruta.get((_m, _p), 0)
        if _pb <= 0: continue
        _cap_mq[f'MP{_m}'] = {'ociosa_h': round(_oc_h[_m], 1), 'potencial_t': round(_oc_h[_m] * _pb, 1)}
    trocas_possiveis.append({**_rw, 'capacidade_por_maquina': _cap_mq})

_n_crit = len(restricoes_criticas)
_gt = sum(r['gap'] for r in _gap_lst)
_fo_v = pyo.value(model.obj)
_crit_str = ', '.join(f"{k}({v['percentual']}%)" for k, v in restricoes_criticas.items()) or 'nenhuma'
diagnostico_mix = (
    f"OTIMIZADOR DE MIX — Cenário 2026\n"
    f"FO: R$ {_fo_v:,.0f}\n"
    f"Restrições críticas/violadas ({_n_crit}): {_crit_str}\n"
    f"Demanda não atendida: {_gt:,.1f}t em {len(_gap_lst)} produtos\n"
    f"Máquinas: MP1 (MA), MP6 (MA), MP7 (MA), MP9 (MA), MP27 (ORT), MP28 (ORT)\n"
    f"Mercados: ME (Externo), MI (Interno), Transferência"
)

# ── Razão de alocação por produto ─────────────────────────────────────────────
razao_alocacao = {}
for _p in sorted(model.produtos):
    _dem = sum(dict_demanda.get((_p, _mc), 0) for _mc in model.mercados)
    if _dem == 0:
        continue
    _mq_dp = [m for m in model.maquinas if dict_prod_por_MP.get((m, _p), 0) == 1]
    if not _mq_dp:
        continue

    _margem_por_maq = {}
    for _m in _mq_dp:
        _rec = sum(
            (pyo.value(model.producao_vendavel[_p, _m, _mc]) or 0) * dict_preco.get((_p, _mc), 0)
            for _mc in model.mercados
        )
        _cst = (pyo.value(model.producao_bruta[_p, _m]) or 0) * dict_custos.get((_p, _m), 0)
        # Se producao_bruta=0 (produto produzido apenas como desclassificado via Constraint_maquinas),
        # o custo do otimizador é zero. Usar producao_vendavel × dict_custos para atribuição correta.
        if _cst < 1.0 and dict_custos.get((_p, _m), 0) > 0:
            _vend_m = sum((pyo.value(model.producao_vendavel[_p, _m, _mc]) or 0) for _mc in model.mercados)
            _cst = _vend_m * dict_custos.get((_p, _m), 0)
        _margem_por_maq[f'MP{_m}'] = round(_rec - _cst, 0)

    _mq_usadas = [
        f'MP{m}' for m in model.maquinas
        if sum((pyo.value(model.producao_vendavel[_p, m, _mc]) or 0)
               for _mc in model.mercados) > 0.1
    ]

    if len(_mq_dp) == 1:
        _razao = 'unica_maquina_habilitada'
    elif not _mq_usadas:
        _razao = 'nao_produzido'
    else:
        _melhor_maq = max(_margem_por_maq, key=_margem_por_maq.get)
        _razao = 'maior_margem' if _melhor_maq in _mq_usadas else 'restricao_de_balanco'

    _impacto_restricoes = {}
    for _m in _mq_dp:
        _coefs = {}
        _esp_ckn_fc_k1 = get_consumo_especifico(model, 'MA', _p, _m, 'CKN-FC-K1')
        _esp_ckn_fl_k2 = get_consumo_especifico(model, 'MA', _p, _m, 'CKN-FL-K2')
        _esp_ckn_fc_k3 = get_consumo_especifico(model, 'MA', _p, _m, 'CKN-FC-K3')
        _esp_ctmp      = get_consumo_especifico(model, 'MA', _p, _m, 'CTMP')
        _esp_ckb_fc    = get_consumo_especifico(model, 'ORT', _p, _m, 'CKB-FC')
        _esp_ckb_fl    = get_consumo_especifico(model, 'ORT', _p, _m, 'CKB-FL')
        _capt_rate     = dict_param_add_ORT.get('Captação de Água (m3/t)', 0)
        _emis_rate     = dict_param_add_ORT.get('Emissário (m3/t)', 0)
        if _esp_ckn_fc_k1 + _esp_ckn_fl_k2 + _esp_ckn_fc_k3 > 1e-9:
            _coefs['Digestores_MA_t_t'] = round(_esp_ckn_fc_k1 + _esp_ckn_fl_k2 + _esp_ckn_fc_k3, 6)
        if _esp_ctmp > 1e-9:
            _coefs['CTMP_t_t'] = round(_esp_ctmp, 6)
        if _esp_ckb_fc > 1e-9:
            _coefs['CKB_FC_t_t'] = round(_esp_ckb_fc, 6)
        if _esp_ckb_fl > 1e-9:
            _coefs['CKB_FL_t_t'] = round(_esp_ckb_fl, 6)
        if _capt_rate > 1e-9 and _m in [27, 28]:
            _coefs['Captacao_m3_t'] = round(_capt_rate, 4)
        if _emis_rate > 1e-9 and _m in [27, 28]:
            _coefs['Emissario_m3_t'] = round(_emis_rate, 4)
        if _coefs:
            _impacto_restricoes[f'MP{_m}'] = _coefs

    razao_alocacao[_p] = {
        'maquinas_habilitadas': [f'MP{m}' for m in _mq_dp],
        'maquinas_usadas':      _mq_usadas,
        'razao':                _razao,
        'margem_por_maquina':   _margem_por_maq,
        'impacto_restricoes':   _impacto_restricoes,
    }

diagnostico_ia = {
    'diagnostico_mix': diagnostico_mix,
    'restricoes_status': restricoes_status,
    'restricoes_criticas': restricoes_criticas,
    'alocacao_produtos': alocacao_produtos,
    'sensibilidade_restricoes': sensibilidade_restricoes,
    'trocas_possiveis': trocas_possiveis,
    'razao_alocacao': razao_alocacao,
}

# ── waste_data: um registro por (produto, máquina) habilitado ─────────────
# Inclui taxas de sobrevivência, deltas de perda, preço, custo variável e
# produtividade bruta (t/h) para alimentar o Waterfall do dashboard.

waste_data = []
for (maquina, produto) in sorted(dict_total_waste.keys()):
    if dict_prod_por_MP.get((maquina, produto), 0) == 0:
        continue

    # Preço: primeiro mercado com preço > 0
    _preco_wf = 0.0
    for _mc in ['ME', 'MI', 'Transferência']:
        _p_mc = dict_preco.get((produto, _mc), 0)
        if _p_mc > 0:
            _preco_wf = _p_mc
            break

    _custo_wf   = dict_custos.get((produto, maquina), 0.0)
    _prod_h_wf  = dict_produtividade_bruta.get((maquina, produto), 0.0)

    # Taxas de sobrevivência acumuladas
    _sIAC      = dict_IAC.get((maquina, produto), 1.0)
    _sRefugo   = 1.0 - dict_refugo_ajustado.get((maquina, produto), 0.0)
    _sRepExt   = _sRefugo   * (1 - dict_Rep_Externo.get((maquina, produto), 0.0))
    _sMR2      = _sRepExt   * (1 - dict_MR2.get((maquina, produto), 0.0))
    _sSala     = _sMR2      * (1 - dict_Sala_Perdas.get((maquina, produto), 0.0))
    _sCortGram = _sSala     * (1 - dict_Cortadeira_Perda_Gramatura.get((maquina, produto), 0.0))
    _sCortCort = _sCortGram * (1 - dict_Cortadeira_Perdas_Cortadeira.get((maquina, produto), 0.0))
    _sEstExp   = _sCortCort * (1 - dict_Estoque_Perdas.get((maquina, produto), 0.0))
    _sVend     = _sEstExp   * (1 - dict_Estoque_Perdas_Refugo.get((maquina, produto), 0.0))

    waste_data.append({
        "produto":             produto,
        "maquina":             maquina,
        "preco":               round(_preco_wf, 2),
        "custo_variavel":      round(_custo_wf, 2),
        "produtividade_bruta": round(_prod_h_wf, 4),
        "surv_IAC":            round(_sIAC,      6),
        "surv_Refugo":         round(_sRefugo,   6),
        "surv_RepExt":         round(_sRepExt,   6),
        "surv_MR2":            round(_sMR2,      6),
        "surv_Sala":           round(_sSala,     6),
        "surv_CortGram":       round(_sCortGram, 6),
        "surv_CortCort":       round(_sCortCort, 6),
        "surv_EstExp":         round(_sEstExp,   6),
        "surv_Vendavel":       round(_sVend,     6),
        "delta_IAC":           round(1.0       - _sIAC,      6),
        "delta_Refugo":        round(_sIAC     - _sRefugo,   6),
        "delta_RepExt":        round(_sRefugo  - _sRepExt,   6),
        "delta_MR2":           round(_sRepExt  - _sMR2,      6),
        "delta_Sala":          round(_sMR2     - _sSala,     6),
        "delta_CortGram":      round(_sSala    - _sCortGram, 6),
        "delta_CortCort":      round(_sCortGram - _sCortCort,6),
        "delta_EstExp":        round(_sCortCort - _sEstExp,  6),
        "delta_EstRef":        round(_sEstExp  - _sVend,     6),
    })

# ── Dados fixos das unidades extras (Angatuba, Goiana, Piracicaba) ─────────
# Premissas: demanda 100% atendida (vendável = dado do Excel), mercado MI,
# utilização 100%, sem horas ociosas. Perdas por unidade conforme abaixo:
#   Bruta→Líquida: Refugo_MP e IAC  |  Líquida→Vendável: Mantas/Refiles e Estoque
_PARAMS_EXTRAS = {
    'Angatuba':   {'refugo_mp': 0.016, 'iac': 0.067, 'mantas_refiles': 0.05,  'estoque': 0.0007},
    'Piracicaba': {'refugo_mp': 0.022, 'iac': 0.067, 'mantas_refiles': 0.04,  'estoque': 0.0},
    'Goiana':     {'refugo_mp': 0.015, 'iac': 0.06,  'mantas_refiles': 0.03,  'estoque': 0.0},
}

def _surv_extras(unidade):
    """Retorna (sIAC, sRefugo, sMR2, sEstExp, sVend) para a unidade extra."""
    p = _PARAMS_EXTRAS[unidade]
    sIAC    = 1.0 - p['iac']
    sRefugo = (1.0 - p['refugo_mp']) * sIAC
    sMR2    = sRefugo * (1.0 - p['mantas_refiles'])
    sEstExp = sMR2    * (1.0 - p['estoque'])
    sVend   = sEstExp
    return sIAC, sRefugo, sMR2, sEstExp, sVend

_data_extras_raw = pd.read_excel(EXCEL_PATH,
                                  sheet_name='Reciclados e Angatuba', header=None)
_data_extras_raw.columns = ['Unidade', 'Produto', 'Producao_Vendavel', 'Preco',
                              'Custo_Variavel', 'Margem_t', 'Margem_total']
_data_extras_raw = _data_extras_raw.iloc[1:].reset_index(drop=True)
for _col_e in ['Producao_Vendavel', 'Preco', 'Custo_Variavel', 'Margem_t', 'Margem_total']:
    _data_extras_raw[_col_e] = pd.to_numeric(_data_extras_raw[_col_e], errors='coerce').fillna(0.0)

_mapa_mp_extras = {'Angatuba': 'MP-Angatuba', 'Goiana': 'MP-Goiana', 'Piracicaba': 'MP-Piracicaba'}

_extras_por_unidade = {}
for _, _row_e in _data_extras_raw.iterrows():
    _un_e = _row_e['Unidade']
    if _un_e not in _extras_por_unidade:
        _extras_por_unidade[_un_e] = {
            'mp': _mapa_mp_extras[_un_e], 'produtos': [],
            'producao_total': 0.0, 'producao_bruta_total': 0.0,
            'margem_total': 0.0, 'custo_total': 0.0, 'receita_total': 0.0,
        }
    _prod_e  = float(_row_e['Producao_Vendavel'])
    _, _, _, _, _sVend_loop = _surv_extras(_un_e)
    _prod_bruta_e = _prod_e / _sVend_loop
    _preco_e = float(_row_e['Preco'])
    _custo_e = float(_row_e['Custo_Variavel'])
    _extras_por_unidade[_un_e]['produtos'].append({
        'produto':         str(_row_e['Produto']),
        'producao':        _prod_e,
        'producao_bruta':  round(_prod_bruta_e, 1),
        'preco':           _preco_e,
        'custo_variavel':  _custo_e,
        'margem_t':        float(_row_e['Margem_t']),
        'margem_total':    float(_row_e['Margem_total']),
    })
    _extras_por_unidade[_un_e]['producao_total']       += _prod_e
    _extras_por_unidade[_un_e]['producao_bruta_total'] += _prod_bruta_e
    _extras_por_unidade[_un_e]['margem_total']         += float(_row_e['Margem_total'])
    _extras_por_unidade[_un_e]['custo_total']          += _prod_e * _custo_e
    _extras_por_unidade[_un_e]['receita_total']        += _prod_e * _preco_e

_extras_producao_total       = sum(v['producao_total']       for v in _extras_por_unidade.values())
_extras_producao_bruta_total = sum(v['producao_bruta_total'] for v in _extras_por_unidade.values())
_extras_perda_waste_total    = _extras_producao_bruta_total - _extras_producao_total
_extras_margem_total         = sum(v['margem_total']         for v in _extras_por_unidade.values())
_extras_custo_total          = sum(v['custo_total']          for v in _extras_por_unidade.values())
_extras_receita_total        = sum(v['receita_total']        for v in _extras_por_unidade.values())

# Adicionar entradas de waste_data para as unidades extras (uma por produto)
for _un_we, _uv_we in _extras_por_unidade.items():
    _sIAC_e, _sRefugo_e, _sMR2_e, _sEstExp_e, _sVend_e = _surv_extras(_un_we)
    for _pd_we in _uv_we['produtos']:
        waste_data.append({
            "produto":             _pd_we['produto'],
            "maquina":             _un_we,          # string: "Angatuba", "Goiana", "Piracicaba"
            "preco":               round(_pd_we['preco'],         2),
            "custo_variavel":      round(_pd_we['custo_variavel'],2),
            "produtividade_bruta": 0.0,             # não aplicável (dados fixos, sem produtividade horária)
            "surv_IAC":            round(_sIAC_e,    6),
            "surv_Refugo":         round(_sRefugo_e, 6),
            "surv_RepExt":         round(_sRefugo_e, 6),
            "surv_MR2":            round(_sMR2_e,    6),
            "surv_Sala":           round(_sMR2_e,    6),
            "surv_CortGram":       round(_sMR2_e,    6),
            "surv_CortCort":       round(_sMR2_e,    6),
            "surv_EstExp":         round(_sEstExp_e, 6),
            "surv_Vendavel":       round(_sVend_e,   6),
            "delta_IAC":           round(1.0        - _sIAC_e,    6),
            "delta_Refugo":        round(_sIAC_e    - _sRefugo_e, 6),
            "delta_RepExt":        0.0,
            "delta_MR2":           round(_sRefugo_e - _sMR2_e,    6),
            "delta_Sala":          0.0,
            "delta_CortGram":      0.0,
            "delta_CortCort":      0.0,
            "delta_EstExp":        round(_sMR2_e    - _sEstExp_e, 6),
            "delta_EstRef":        0.0,
        })

# ── bridge_data: dados para a bridge macro do dashboard ───────────────────
# Estrutura: Capacidade Instalada → Ociosa → Bruta Real → Gap Demanda → Waste → Vendável

_maquinas_lista = [1, 6, 7, 9, 27, 28, 25, 26, 12, 13, 16, 23]

# Capacidade instalada: para cada máquina, tempo disponível × maior produtividade habilitada
_capac_instalada_br = 0.0
for _m_br in _maquinas_lista:
    _prods_hab_br = [p for p in lista_produtos if dict_produtividade_bruta.get((_m_br, p), 0) > 0]
    if not _prods_hab_br:
        continue
    _prod_max_br = max(dict_produtividade_bruta.get((_m_br, p), 0) for p in _prods_hab_br)
    _t_disp_br   = dict_tempo_carga.get(_m_br, 0) * dict_taxa_DISP.get(_m_br, 1)
    _capac_instalada_br += _t_disp_br * _prod_max_br

# Capacidade ociosa em toneladas: horas ociosas × produtividade média das habilitadas
_capac_ociosa_br = 0.0
for _m_br in _maquinas_lista:
    _h_oc_br = pyo.value(model.capacidade_ociosa[_m_br]) or 0
    if _h_oc_br <= 0:
        continue
    _prods_hab_br = [p for p in lista_produtos if dict_produtividade_bruta.get((_m_br, p), 0) > 0]
    if not _prods_hab_br:
        continue
    _prod_med_br = sum(dict_produtividade_bruta.get((_m_br, p), 0) for p in _prods_hab_br) / len(_prods_hab_br)
    _capac_ociosa_br += _h_oc_br * _prod_med_br

# Produção bruta e vendável reais
_bruta_real_br = sum(
    pyo.value(model.producao_bruta[p, m]) or 0
    for p in lista_produtos for m in _maquinas_lista
)
_vendavel_real_br = sum(
    pyo.value(model.producao_vendavel[p, m, merc]) or 0
    for p in lista_produtos for m in _maquinas_lista for merc in ['ME', 'MI', 'Transferência']
)
_perda_waste_br = _bruta_real_br - _vendavel_real_br

# Gap de demanda (demanda total não atendida)
_demanda_total_br = sum(dict_demanda.values())
_gap_demanda_br   = sum(
    pyo.value(model.demanda_nao_atendida[p, merc]) or 0
    for p in model.produtos for merc in model.mercados
)

# Preço médio ponderado pela produção vendável (receita total / vendável total)
_receita_total_br = sum(
    (pyo.value(model.producao_vendavel[p, m, merc]) or 0) * dict_preco.get((p, merc), 0)
    for p in model.produtos for m in model.maquinas for merc in model.mercados
)
_preco_medio_br = (_receita_total_br / _vendavel_real_br) if _vendavel_real_br > 0 else 0

# Custo médio ponderado pela produção bruta
_custo_total_br = sum(
    (pyo.value(model.producao_bruta[p, m]) or 0) * dict_custos.get((p, m), 0)
    for p in model.produtos for m in model.maquinas
)
_custo_medio_br = (_custo_total_br / _bruta_real_br) if _bruta_real_br > 0 else 0

bridge_data = {
    "capac_instalada_t":      round(_capac_instalada_br, 1),
    "capac_ociosa_t":         round(_capac_ociosa_br, 1),
    "producao_bruta_real_t":  round(_bruta_real_br, 1),
    "gap_demanda_t":          round(_gap_demanda_br, 1),
    "perda_waste_t":          round(_perda_waste_br, 1),
    "producao_vendavel_t":    round(_vendavel_real_br, 1),
    "demanda_total_t":        round(_demanda_total_br, 1),
    "pct_capac_ociosa":       round(100 * _capac_ociosa_br  / _capac_instalada_br, 2) if _capac_instalada_br > 0 else 0,
    "pct_gap_demanda":        round(100 * _gap_demanda_br   / _capac_instalada_br, 2) if _capac_instalada_br > 0 else 0,
    "pct_waste":              round(100 * _perda_waste_br   / _capac_instalada_br, 2) if _capac_instalada_br > 0 else 0,
    "pct_vendavel":           round(100 * _vendavel_real_br / _capac_instalada_br, 2) if _capac_instalada_br > 0 else 0,
    "preco_medio_ponderado":  round(_preco_medio_br, 2),
    "custo_medio_ponderado":  round(_custo_medio_br, 2),
    "margem_media_ponderada": round(_preco_medio_br - _custo_medio_br, 2),
}

# Atualizar bridge_data com produção das unidades extras (perda bruta→vendável de ~7,69%)
bridge_data["capac_instalada_t"]     = round(bridge_data["capac_instalada_t"]     + _extras_producao_bruta_total, 1)
bridge_data["producao_bruta_real_t"] = round(bridge_data["producao_bruta_real_t"] + _extras_producao_bruta_total, 1)
bridge_data["producao_vendavel_t"]   = round(bridge_data["producao_vendavel_t"]   + _extras_producao_total,       1)
bridge_data["perda_waste_t"]         = round(bridge_data["perda_waste_t"]         + _extras_perda_waste_total,    1)
_new_cap_br  = bridge_data["capac_instalada_t"]
_new_vend_br = bridge_data["producao_vendavel_t"]
_new_brut_br = bridge_data["producao_bruta_real_t"]
bridge_data["pct_capac_ociosa"] = round(100 * bridge_data["capac_ociosa_t"] / _new_cap_br, 2) if _new_cap_br > 0 else 0
bridge_data["pct_gap_demanda"]  = round(100 * bridge_data["gap_demanda_t"]  / _new_cap_br, 2) if _new_cap_br > 0 else 0
bridge_data["pct_waste"]        = round(100 * bridge_data["perda_waste_t"]  / _new_cap_br, 2) if _new_cap_br > 0 else 0
bridge_data["pct_vendavel"]     = round(100 * _new_vend_br / _new_cap_br, 2) if _new_cap_br > 0 else 0
_new_rec_br = _receita_total_br + _extras_receita_total
_new_cst_br = _custo_total_br   + _extras_custo_total
bridge_data["preco_medio_ponderado"]  = round(_new_rec_br / _new_vend_br, 2) if _new_vend_br > 0 else 0
bridge_data["custo_medio_ponderado"]  = round(_new_cst_br / _new_brut_br, 2) if _new_brut_br > 0 else 0
bridge_data["margem_media_ponderada"] = round(
    bridge_data["preco_medio_ponderado"] - bridge_data["custo_medio_ponderado"], 2
)

# ── Coleta os resultados para o dashboard ──────────────────────────────────

# Mapa de restrições CP implementadas: nome Excel → (valor calculado, unidade display)
_cp_calculados = {
    'Cozimento':      (pyo.value(calc_cozimento_CP(model)),     'ADt/h'),
    'Lavagem 1':      (pyo.value(calc_lavagem1_CP(model)),      'ADt/h'),
    'Lavagem 2':      (pyo.value(calc_lavagem2_CP(model)),      'ADt/h'),
    'Deslignificação':(pyo.value(calc_lavagem2_CP(model)),      'ADt/h'),
    'Pulper':         (pyo.value(calc_pulper_CP(model)),        'ADt/h'),
    'CR2':            (pyo.value(calc_cr2_CP(model)),           'tss/d'),
    'Caustificação':  (pyo.value(calc_caustificacao_CP(model)), 'm³ Lb/d'),
    'Evaporação':     (pyo.value(calc_evaporacao_CP(model)),    'tv/h'),
}

dashboard_data = {

    # KPI cards
    "fo_reais": pyo.value(model.obj),
    "producao_bruta_total": sum(
        pyo.value(model.producao_bruta[p, m]) or 0
        for p in model.produtos for m in model.maquinas
        if (pyo.value(model.producao_bruta[p, m]) or 0) > 0
    ),
    "producao_vendavel_total": sum(
        pyo.value(model.producao_vendavel[p, m, merc]) or 0
        for p in model.produtos for m in model.maquinas for merc in model.mercados
        if (pyo.value(model.producao_vendavel[p, m, merc]) or 0) > 0
    ),

    # Produção bruta por máquina
    "bruta_por_maquina": {
        str(m): sum(
            pyo.value(model.producao_bruta[p, m]) or 0
            for p in model.produtos
        )
        for m in model.maquinas
    },

    # Produção vendável por mercado
    "vendavel_por_mercado": {
        merc: sum(
            pyo.value(model.producao_vendavel[p, m, merc]) or 0
            for p in model.produtos for m in model.maquinas
        )
        for merc in model.mercados
    },

    # Cobertura de demanda por produto (top 10 por volume de demanda)
    "cobertura_por_produto": [
        {
            "produto": p,
            "mercado": merc,
            "demanda": qtd,
            "atendida": sum(pyo.value(model.producao_vendavel[p, m, merc]) or 0 for m in model.maquinas),
            "nao_atendida": pyo.value(model.demanda_nao_atendida[p, merc]),
        }
        for (p, merc), qtd in dict_demanda.items()
        if qtd > 0
    ],

    # Capacidade por máquina
    "capacidade_por_maquina": {
        str(m): {
            "usado": sum(
                (pyo.value(model.producao_bruta[p, m]) or 0) / dict_produtividade_bruta.get((m, p), 1)
                for p in model.produtos
                if dict_produtividade_bruta.get((m, p), 0) > 0
            ),
            "disponivel": dict_tempo_carga.get(m, 0) * dict_taxa_DISP.get(m, 1),
            "ocioso": pyo.value(model.capacidade_ociosa[m]),
        }
        for m in model.maquinas
    },

    # Balanço de fábrica MA
    "balanco_MA": [
        {"nome": "Evaporação",        "usado": pyo.value(calc_evaporacao(model)),              "limite": dict_capacidade_MSR.get("Evaporação", 0)},
        {"nome": "Licor Verde",       "usado": pyo.value(calc_licor_verde(model)),              "limite": dict_capacidade_MSR.get("Licor verde", 0)},
        {"nome": "PMAD",              "usado": pyo.value(calc_pmad(model)),                     "limite": dict_capacidade_MSR.get("PMAD", 0)},
        {"nome": "Sólidos c/ Cinzas", "usado": pyo.value(calc_tss_total(model, "MA")),          "limite": dict_capacidade_MSR.get("Sólidos c/ cinzas", 0)},
        {"nome": "Digestor Esco",     "usado": pyo.value(calc_producao_digestor_MA(model, "esco")),  "limite": dict_capacidade_MSR.get("Esco", 0)},
        {"nome": "Digestor Kamyr",    "usado": pyo.value(calc_producao_digestor_MA(model, "kamyr")), "limite": dict_capacidade_MSR.get("Kamyr", 0)},
        {"nome": "Digestor CTMP",     "usado": pyo.value(calc_producao_digestor_MA(model, "ctmp")),  "limite": dict_capacidade_MSR.get("CTMP", 0)},
    ],

    # Balanço de fábrica ORT
    "balanco_ORT": [
        {"nome": "Caustificação",     "usado": pyo.value(calc_caustificacao(model)), "limite": dict_emissario.get("Caustificação", 0)},
        {"nome": "Outorga Captação",  "usado": pyo.value(calc_captacao(model)),      "limite": dict_emissario.get("Outorga Captação", 0)},
        {"nome": "Outorga Emissário", "usado": pyo.value(calc_emissario(model)),     "limite": dict_emissario.get("Outorga Emissario", 0)},
        {"nome": "CDR",               "usado": pyo.value(calc_cdr(model)),           "limite": dict_emissario.get("CDR", 0)},
        {"nome": "Evaporação ORT",    "usado": pyo.value(calc_evaporacao_ORT(model)),"limite": dict_emissario.get("Evaporação", 0)},
        *[
            {"nome": fibra, "usado": pyo.value(calc_producao_digestor_ORT(model, fibra)), "limite": dict_emissario.get(fibra, 0)}
            for fibra in lista_fibras_ORT
        ],
    ],

    # Balanço de fábrica OTA
    "balanco_OTA": [
        {"nome": "Lavagem (L2+L4)",  "usado": pyo.value(calc_lavagem_OTA(model)),      "limite": _lim_lavagem_OTA},
        {"nome": "Cozimento",        "usado": pyo.value(calc_cozimento_OTA(model)),    "limite": _lim_cozimento_OTA},
        {"nome": "CDR4",             "usado": pyo.value(calc_cdr_OTA(model)),          "limite": _lim_cdr4_OTA},
        {"nome": "Caustificação",    "usado": pyo.value(calc_caustificacao_OTA(model)),"limite": _lim_caustificacao_OTA},
        {"nome": "Evaporação",       "usado": pyo.value(calc_evaporacao_OTA(model)),   "limite": _lim_evaporacao_OTA},
    ],

    # Balanço de fábrica CP — restrições MSR disponíveis
    "balanco_CP": [
        {
            "nome":               restricao,
            "usado":              round(_cp_calculados[restricao][0], 2) if restricao in _cp_calculados else 0,
            "limite":             float(valor) if valor < 1e8 else None,
            "unidade":            _cp_calculados[restricao][1] if restricao in _cp_calculados else dict_unidades_CP.get(restricao, ''),
            "em_desenvolvimento": restricao not in _cp_calculados,
        }
        for restricao, valor in dict_restricoes_CP.items()
        if valor < 1e8
    ],

    # Produção vendável por máquina × produto — soma dos mercados (para filtro do gráfico)
    "atendida_por_maquina_produto": {
        p: {
            str(m): round(sum(
                pyo.value(model.producao_vendavel[p, m, merc]) or 0
                for merc in model.mercados
            ), 1)
            for m in model.maquinas
        }
        for p in model.produtos
        if sum(
            pyo.value(model.producao_vendavel[p, m, merc]) or 0
            for m in model.maquinas
            for merc in model.mercados
        ) > 0.1
    },

    # Máquinas utilizadas por produto × mercado (string para tabela de cobertura)
    "maquinas_por_produto_mercado": [
        {
            "produto": p,
            "mercado": merc,
            "maquinas": {
                str(m): round(pyo.value(model.producao_vendavel[p, m, merc]), 1)
                for m in model.maquinas
                if pyo.value(model.producao_vendavel[p, m, merc]) > 0.1
            }
        }
        for (p, merc), qtd in dict_demanda.items()
        if qtd > 0
    ],

    # Margem de contribuição real por máquina (receita − custo variável)
    "margem_por_maquina": {
    str(m): round(
        sum(
            (pyo.value(model.producao_vendavel[p, m, merc]) or 0) * dict_preco.get((p, merc), 0)
            for p in model.produtos
            for merc in model.mercados
        ) - sum(
            (pyo.value(model.producao_bruta[p, m]) or 0) * (
                dict_custos.get((p, m), 0)
                + flag_remuneracao_celulose * (
                    get_consumo_especifico(model, 'ORT', p, m, 'CKB-FC') * (preco_venda_fibra_curta - custo_variavel_fibra_curta)
                    + get_consumo_especifico(model, 'ORT', p, m, 'CKB-FL') * (preco_venda_fibra_longa - custo_variavel_fibra_longa)
                )
            )
            for p in model.produtos
        ),
        0
    )
    for m in model.maquinas
},

    # Custo variável por máquina (Σ producao_bruta × dict_custos)
    "custo_por_maquina": {
    str(m): round(
        sum(
            (pyo.value(model.producao_bruta[p, m]) or 0) * dict_custos.get((p, m), 0)
            for p in model.produtos
        ),
        0
    )
    for m in model.maquinas
},

    # Capacidade máxima bruta por máquina (da aba Máquinas do Excel)
    "prod_bruta_max_por_maquina": {
    str(m): (
        float(dict_Prod_bruta_max[m]) if dict_Prod_bruta_max.get(m, 1e9) < 1e8
        else round(
            dict_tempo_carga.get(m, 0) * dict_taxa_DISP.get(m, 1)
            * (
                sum(pyo.value(model.producao_bruta[p, m]) or 0 for p in model.produtos)
                / max(
                    sum(
                        (pyo.value(model.producao_bruta[p, m]) or 0) / dict_produtividade_bruta[(m, p)]
                        for p in model.produtos
                        if dict_produtividade_bruta.get((m, p), 0) > 0
                    ),
                    1e-9
                )
            ), 1
        )
    )
    for m in model.maquinas
},

    **diagnostico_ia,

    # Dados de Total Waste por produto × máquina (substitui WF_DATA hardcoded no HTML)
    "waste_data": waste_data,

    # Dados para bridge macro: Capacidade Instalada → Vendável
    "bridge_data": bridge_data,
}

# Integrar unidades extras (Angatuba, Goiana, Piracicaba) no dashboard_data
dashboard_data["fo_reais"]                += _extras_margem_total
dashboard_data["producao_bruta_total"]    += _extras_producao_bruta_total
dashboard_data["producao_vendavel_total"] += _extras_producao_total

for _uv_d in _extras_por_unidade.values():
    _mp_d = _uv_d['mp']
    dashboard_data["bruta_por_maquina"][_mp_d]          = round(_uv_d['producao_bruta_total'], 1)
    dashboard_data["margem_por_maquina"][_mp_d]         = round(_uv_d['margem_total'], 0)
    dashboard_data["custo_por_maquina"][_mp_d]          = round(_uv_d['custo_total'], 0)
    dashboard_data["prod_bruta_max_por_maquina"][_mp_d] = round(_uv_d['producao_bruta_total'], 1)
    # capacidade expressa em toneladas (bruta): utilização = 100%, sem ociosidade
    dashboard_data["capacidade_por_maquina"][_mp_d] = {
        "usado":      _uv_d['producao_bruta_total'],
        "disponivel": _uv_d['producao_bruta_total'],
        "ocioso":     0.0,
    }

dashboard_data["vendavel_por_mercado"]["MI"] = (
    dashboard_data["vendavel_por_mercado"].get("MI", 0) + _extras_producao_total
)

for _uv_d in _extras_por_unidade.values():
    for _pd_d in _uv_d['produtos']:
        dashboard_data["cobertura_por_produto"].append({
            "produto": _pd_d['produto'], "mercado": "MI",
            "demanda": _pd_d['producao'], "atendida": _pd_d['producao'], "nao_atendida": 0.0,
        })
        if _pd_d['produto'] not in dashboard_data["atendida_por_maquina_produto"]:
            dashboard_data["atendida_por_maquina_produto"][_pd_d['produto']] = {}
        dashboard_data["atendida_por_maquina_produto"][_pd_d['produto']][_uv_d['mp']] = round(_pd_d['producao'], 1)
        dashboard_data["maquinas_por_produto_mercado"].append({
            "produto": _pd_d['produto'], "mercado": "MI",
            "maquinas": {_uv_d['mp']: round(_pd_d['producao'], 1)},
        })

dashboard_data["unidades_extras"] = {
    _un_d: {
        "mp":                    _uv_d['mp'],
        "producao_vendavel_total": round(_uv_d['producao_total'],       1),
        "producao_bruta_total":    round(_uv_d['producao_bruta_total'], 1),
        "perda_waste_t":           round(_uv_d['producao_bruta_total'] - _uv_d['producao_total'], 1),
        "pct_waste":               round(_REFUGO_AJ_EXTRAS * 100, 2),
        "margem_total":            round(_uv_d['margem_total'], 0),
        "custo_total":             round(_uv_d['custo_total'], 0),
        "receita_total":           round(_uv_d['receita_total'], 0),
        "produtos":                _uv_d['produtos'],
        "mercado":                 "MI",
        "utilization_pct":         100.0,
        "horas_ociosas":           0.0,
    }
    for _un_d, _uv_d in _extras_por_unidade.items()
}

if _IS_LAMBDA:
    import tempfile
    _tmp_json = '/tmp/dashboard_data.json'
    with open(_tmp_json, 'w', encoding='utf-8') as f:
        json.dump(dashboard_data, f, ensure_ascii=False, indent=2)
    boto3.client('s3').upload_file(_tmp_json, S3_BUCKET, JSON_KEY)
    print(f"\n✓ dashboard_data.json enviado para s3://{S3_BUCKET}/{JSON_KEY}")
else:
    with open("dashboard_data.json", "w", encoding="utf-8") as f:
        json.dump(dashboard_data, f, ensure_ascii=False, indent=2)
    print("\n✓ dashboard_data.json gerado — abra dashboard_mix.html no navegador")


m = 26
t_disp = dict_tempo_carga[m] * dict_taxa_DISP[m]
t_usado = sum((pyo.value(model.producao_bruta[p, m]) or 0) / dict_produtividade_bruta[(m, p)]
              for p in model.produtos if dict_produtividade_bruta.get((m, p), 0) > 0)
prod_total = sum(pyo.value(model.producao_bruta[p, m]) or 0 for p in model.produtos)
prod_max_bruta = max(v for (mm, p), v in dict_produtividade_bruta.items() if mm == m)

print(f"MC26 | t_disp={t_disp:.1f}h  t_usado={t_usado:.1f}h  ociosa={t_disp-t_usado:.1f}h")
print(f"MC26 | produção={prod_total:,.0f}t  linha_teórica={t_disp*prod_max_bruta:,.0f}t  gap={t_disp*prod_max_bruta-prod_total:,.0f}t")
for p in sorted(model.produtos):
    v = pyo.value(model.producao_bruta[p, m]) or 0
    if v > 1:
        pb = dict_produtividade_bruta[(m, p)]
        print(f"   {p:<15} {v:>12,.0f} t  @ {pb:.2f} t/h  = {v/pb:>8,.1f} h")

# ── Tabela CP: Bruta e Vendável Real vs Necessária para 100% da Demanda ──────

_PREFIXOS_CP_ALL = _PREFIXOS_CKN_CP + _PREFIXOS_CKD_CP + _PREFIXOS_CKB_CP
_MAQUINAS_CP_TAB = [16, 23]

_prods_cp_tab = sorted([
    p for p in model.produtos
    if str(p).startswith(_PREFIXOS_CP_ALL)
    and sum(dict_demanda.get((p, mc), 0) for mc in model.mercados) > 0
])

_W = 160
print("\n" + "="*_W)
print("TABELA CP — BRUTA / VENDÁVEL REAL vs NECESSÁRIA PARA ATENDER 100% DA DEMANDA")
print("    Bruta Nec. = Demanda (produto) ÷ taxa de sobrevivência bruta→vendável da máquina")
print("    Δ Bruta    = Bruta Nec. − Bruta Real    |    Δ Vendável = Demanda − Vendável Real")
print("="*_W)
print(f"  {'Produto':<14} {'Máq':>4} {'Tipo':>5}  "
      f"{'Demanda (t)':>13}  {'Bruta Real (t)':>14}  {'Vendável Real (t)':>17}  "
      f"{'Surv%':>6}  {'Bruta Nec. (t)':>14}  {'Δ Bruta (t)':>13}  {'Δ Vendável (t)':>14}")
print("-"*_W)

_tot_dem = 0.0
_tot_br  = 0.0
_tot_vr  = 0.0
_tot_bn  = 0.0
_tot_db  = 0.0
_tot_dv  = 0.0

_prods_ja_contados = set()

for _p in _prods_cp_tab:
    _dem_p = sum(dict_demanda.get((_p, mc), 0) for mc in model.mercados)
    _tipo  = ('CKN' if str(_p).startswith(_PREFIXOS_CKN_CP)
              else 'CKD' if str(_p).startswith(_PREFIXOS_CKD_CP) else 'CKB')

    _maq_hab = [_m for _m in _MAQUINAS_CP_TAB if dict_prod_por_MP.get((_m, _p), 0) == 1]
    _bruta_total_p = sum((pyo.value(model.producao_bruta[_p, _m]) or 0) for _m in _maq_hab)
    _vend_total_p  = sum((pyo.value(model.producao_vendavel[_p, _m, mc]) or 0)
                         for _m in _maq_hab for mc in model.mercados)
    _gap_vend_p    = _dem_p - _vend_total_p

    for _m in _maq_hab:
        _br = pyo.value(model.producao_bruta[_p, _m]) or 0
        _vr = sum((pyo.value(model.producao_vendavel[_p, _m, mc]) or 0) for mc in model.mercados)

        # Sobrevivência bruta → vendável (refugo ajustado × complemento do waste)
        _surv = (1.0 - dict_refugo_ajustado.get((_m, _p), 0.0)) * (1.0 - dict_waste.get((_m, _p), 0.0))
        _surv = max(_surv, 1e-9)

        # Fração da demanda alocada a esta máquina (proporcional à bruta real; 1/n se nada produzido)
        if len(_maq_hab) == 1:
            _frac = 1.0
        elif _bruta_total_p > 0:
            _frac = _br / _bruta_total_p
        else:
            _frac = 1.0 / len(_maq_hab)

        _dem_m = _dem_p * _frac
        _bn    = _dem_m / _surv
        _db    = _bn - _br
        _dv    = _gap_vend_p * _frac

        # Flag visual para itens não atendidos
        _flag = ' ◄' if _dv > 0.5 else ''

        print(f"  {_p:<14} {_m:>4} {_tipo:>5}  "
              f"{_dem_m:>13,.1f}  {_br:>14,.1f}  {_vr:>17,.1f}  "
              f"{_surv*100:>5.1f}%  {_bn:>14,.1f}  {_db:>13,.1f}  {_dv:>14,.1f}{_flag}")

        # Acumular totais (demanda contada por máquina proporcional para não duplicar)
        _tot_dem += _dem_m
        _tot_br  += _br
        _tot_vr  += _vr
        _tot_bn  += _bn
        _tot_db  += _db
        _tot_dv  += _dv

print("-"*_W)
_dem_total_cp  = sum(sum(dict_demanda.get((_p, mc), 0) for mc in model.mercados) for _p in _prods_cp_tab)
_bruta_cp_real = sum((pyo.value(model.producao_bruta[_p, _m]) or 0)
                     for _p in _prods_cp_tab for _m in _MAQUINAS_CP_TAB)
_vend_cp_real  = sum((pyo.value(model.producao_vendavel[_p, _m, mc]) or 0)
                     for _p in _prods_cp_tab for _m in _MAQUINAS_CP_TAB for mc in model.mercados)
print(f"  {'TOTAL CP':<14} {'':>4} {'':>5}  "
      f"{_dem_total_cp:>13,.1f}  {_bruta_cp_real:>14,.1f}  {_vend_cp_real:>17,.1f}  "
      f"{'':>6}  {_tot_bn:>14,.1f}  {_tot_db:>13,.1f}  {_dem_total_cp - _vend_cp_real:>14,.1f}")
print("="*_W)
print(f"\n  Demanda total CP:          {_dem_total_cp:>12,.1f} t")
print(f"  Produção bruta real CP:    {_bruta_cp_real:>12,.1f} t")
print(f"  Produção vendável real CP: {_vend_cp_real:>12,.1f} t")
print(f"  Bruta adicional necessária:{_tot_db:>12,.1f} t  (para atender 100% da demanda)")
print(f"  Gap total vendável:        {_dem_total_cp - _vend_cp_real:>12,.1f} t")
print(f"\n  Produtos com gap (◄): ver linhas marcadas acima")

_adt_gap = _tot_db * _BASE_SECA_CP / _BASE_UMIDA_CP
_refugo_ckd_nec = _adt_gap
_refugo_ckn_nec = _adt_gap * _GER_SOLIDOS_CKD_CP / _GER_SOLIDOS_CKN_CP
print(f"\n  REFUGO ADICIONAL NECESSÁRIO PARA FECHAR O GAP")
print(f"  {'-'*55}")
print(f"  Gap em fibra (ADt/ano):            {_adt_gap:>10,.1f}")
print(f"  Refugo CKD necessário:             {_refugo_ckd_nec:>10,.1f}  ADt/ano")
print(f"  Refugo CKN necessário (alternativo):{_refugo_ckn_nec:>9,.1f}  ADt/ano  (×{_GER_SOLIDOS_CKD_CP/_GER_SOLIDOS_CKN_CP:.2f} — libera menos CR2/ADt)")