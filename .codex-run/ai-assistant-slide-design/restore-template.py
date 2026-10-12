import copy
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from lxml import etree as E
p=Path(r'D:\AIassist\.codex-run\ai-assistant-slide-design')
ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
st=E.parse(str(p/'template-slide1.xml')).find('.//p:spTree',ns)
ref={el.xpath('.//p:cNvPr/@id',namespaces=ns)[0]:el for el in st if el.xpath('.//p:cNvPr/@id',namespaces=ns)}
with ZipFile(p/'revised-generated.pptx') as z:parts={n:z.read(n) for n in z.namelist()}
r=E.fromstring(parts['ppt/slides/slide1.xml']);tree=r.find('.//p:spTree',ns)
objs=[el for el in tree if E.QName(el).localname not in ['nvGrpSpPr','grpSpPr']]
for idx,rid in [(1,'56'),(2,'57'),(3,'59'),(4,'61')]:
    new=copy.deepcopy(ref[rid]);new.find('.//p:cNvPr',ns).set('id',str(1048901+idx));tree.replace(objs[idx],new)
title=copy.deepcopy(ref['1048900'])
for t,v in zip(title.xpath('.//a:t',namespaces=ns),['4 .','最新','工作与成效','—','AI助手','智能辅助审查']):t.text=v
tree.insert(3,title)
parts['ppt/slides/slide1.xml']=E.tostring(r,xml_declaration=True,encoding='UTF-8',standalone=True)
with ZipFile(p/'revised-candidate.pptx','w',ZIP_DEFLATED) as z:
    for n,b in parts.items():z.writestr(n,b)
print('Restored template geometry and typography')
