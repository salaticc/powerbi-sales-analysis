"""Audit the supplied PBIX and build an independent, readable aggregate view.

This script reads the model; it does not execute DAX or rewrite the PBIX.
"""
from pathlib import Path
import hashlib,json,sys
import pandas as pd
from pbixray import PBIXRay
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.portfolio_ui import write_overview


def main():
    path=ROOT/'report/sales-analysis.pbix'
    model=PBIXRay(str(path))
    sales=model.get_table('Продажи')
    control=model.get_table('Контроль_Продаж')
    products=model.get_table('СПР_продукты')
    rates=model.get_table('СПР_стоимость_доставки')
    relationships=model.relationships
    metrics={'Строк продаж':len(sales),'Уникальных заказов':sales.Order_ID.nunique(),'Уникальных покупателей':sales['Покупатель'].nunique(),'Выручка':float(sales['Выручка'].sum()),'Прибыль':float(sales['Прибыль'].sum())}
    checks=[]
    for r in control.itertuples(index=False,name=None):
        actual=metrics[r[0]]
        checks.append({'metric':r[0],'stored_control':float(r[1]),'recomputed_python':float(actual),'matches':abs(actual-float(r[1]))<.005})
    if not all(x['matches'] for x in checks):raise AssertionError('Embedded control totals do not reconcile')
    if not products['Продукт'].is_unique:raise ValueError('Product dimension is not unique')
    enriched=sales.merge(products[['Продукт','Категория продукта','Подкатегория продукта']],on='Продукт',how='left',validate='many_to_one')
    if enriched['Категория продукта'].isna().any():raise ValueError('Unmatched product keys')
    regions=sales.groupby('Регион').agg(revenue=('Выручка','sum'),profit=('Прибыль','sum'),orders=('Order_ID','nunique')).reset_index()
    category=enriched.groupby('Категория продукта').agg(revenue=('Выручка','sum'),profit=('Прибыль','sum')).reset_index()
    orders=sales.groupby('Order_ID').agg(profit=('Прибыль','sum'),revenue=('Выручка','sum'))
    order_regions=sales.groupby(['Order_ID','Регион'])['Прибыль'].sum()
    any_loss_part=order_regions[order_regions.lt(0)].index.get_level_values('Order_ID').nunique()
    multi_region=sales.groupby('Order_ID')['Регион'].nunique().gt(1).sum()
    rate_map=rates.set_index('Метод доставки')['Стоимость доставки']
    if not set(sales['Метод доставки']).issubset(rate_map.index):raise ValueError('Unmatched shipping method')
    row_shipping=float(sales['Метод доставки'].map(rate_map).sum())
    order_shipping=float(sales[['Order_ID','Метод доставки']].drop_duplicates()['Метод доставки'].map(rate_map).sum())
    has_date_relationship=bool(((relationships.FromTableName.eq('Продажи') & relationships.FromColumnName.eq('Дата заказа') & relationships.ToTableName.eq('Календарь')) | (relationships.ToTableName.eq('Продажи') & relationships.ToColumnName.eq('Дата заказа') & relationships.FromTableName.eq('Календарь'))).any())
    summary={**metrics,'margin_pct':metrics['Прибыль']/metrics['Выручка']*100,'mean_order_value':metrics['Выручка']/metrics['Уникальных заказов'],'date_min':sales['Дата заказа'].min(),'date_max':sales['Дата заказа'].max(),'control_checks':checks,'measures':len(model.dax_measures),'report_pages':4,'negative_total_profit_orders':int(orders.profit.lt(0).sum()),'orders_with_any_negative_region_part':int(any_loss_part),'orders_in_multiple_regions':int(multi_region),'calendar_relationship_present':has_date_relationship,'shipping_by_line_as_original_dax':row_shipping,'shipping_by_order_method_alternative':order_shipping,'pbix_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'currency':'not specified in source; no currency symbol invented','verification':'Python arithmetic and model metadata; native Power BI rendering and DAX filter execution were not performed'}
    (ROOT/'model').mkdir(exist_ok=True);(ROOT/'reports').mkdir(exist_ok=True)
    model.dax_measures.to_json(ROOT/'model/measures.json',orient='records',force_ascii=False,indent=2)
    relationships.to_json(ROOT/'model/relationships.json',orient='records',force_ascii=False,indent=2)
    model.schema.to_json(ROOT/'model/schema.json',orient='records',force_ascii=False,indent=2)
    (ROOT/'reports/summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str))
    for n,frame in [('regions',regions),('categories',category),('control_reconciliation',pd.DataFrame(checks))]:frame.to_csv(ROOT/'reports'/f'{n}.csv',index=False)
    data={'title':'Power BI — продажи и прибыльность','subtitle':'Снимок модели: 01.01–29.04.2022 · 4 страницы отчёта · 19 DAX-мер',
    'kpis':[{'label':'Выручка · единицы источника','value':f"{metrics['Выручка']:,.2f}".replace(',',' ')},{'label':'Прибыль из исходного поля','value':f"{metrics['Прибыль']:,.2f}".replace(',',' ')},{'label':'Уникальных заказов','value':str(metrics['Уникальных заказов'])},{'label':'Контрольных сверок','value':f'{sum(x["matches"] for x in checks)} / {len(checks)}'}],
    'charts':[{'title':'Выручка по категориям','unit':'единицы источника','rows':[{'label':r[0],'value':round(float(r[1]),2)} for r in category.itertuples(index=False,name=None)]},{'title':'Прибыль по регионам','unit':'единицы источника','rows':[{'label':r['Регион'],'value':round(float(r['profit']),2)} for _,r in regions.iterrows()]}],
    'insights':[{'fact':'Technology формирует около 50,91% выручки в исходном периоде.','next':'Сравнить категории также по прибыли и ассортименту: большой оборот сам по себе не определяет приоритет инвестиций.'},{'fact':f"{int(orders.profit.lt(0).sum())} из {metrics['Уникальных заказов']} заказов имеют отрицательную итоговую прибыль по полю источника.",'next':'Разобрать состав этих заказов, категории и расходы. Четыре заказа пересекают регионы, поэтому уровень агрегации влияет на результат.'},{'fact':'Все пять независимых контрольных итогов совпали.','next':'Перед использованием нативного отчёта проверить связь календаря и определение тарифа доставки; замечания и DAX-предложения описаны в MODEL_AUDIT.md.'}],
    'scope':'Это независимый обзор агрегатов из приложенного PBIX, не скриншот Power BI. Валюта и состав поля «Прибыль» не определены источником. ABC в PBIX статичен за весь период; сравнение затрат доставки зависит от единицы тарифа.'}
    sales['month']=pd.to_datetime(sales['Дата заказа']).dt.strftime('%Y-%m')
    month=sales.groupby('month')['Выручка'].sum()
    data['charts'].append({'title':'Выручка по месяцам','unit':'единицы источника','rows':[{'label':str(k),'value':round(float(v),2)} for k,v in month.items()]})
    write_overview(ROOT,data)
    print(json.dumps(summary,ensure_ascii=False,default=str))


if __name__=='__main__':main()
