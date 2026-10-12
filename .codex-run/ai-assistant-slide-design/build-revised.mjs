import fs from 'node:fs/promises';
import {Presentation,PresentationFile} from '@oai/artifact-tool';
const dir='D:/AIassist/.codex-run/ai-assistant-slide-design';
const p=Presentation.create({slideSize:{width:1280,height:720}}),s=p.slides.add();
s.background.fill='#FFFFFF';
const C={blue:'#155DA5',pale:'#C9DAF0',soft:'#F2F2F2',ink:'#000000',red:'#A53533',stroke:'#101843',lav:'#CFD7FF',ice:'#8DC0FF'};
let seq=0;
function sh(g,x,y,w,h,fill='none',stroke='none',lw=0){return s.shapes.add({geometry:g,name:`图形-${++seq}`,position:{left:x,top:y,width:w,height:h},fill,line:{fill:stroke,width:lw,style:'solid'}})}
function tx(t,x,y,w,h,sz=16,c=C.ink,b=false,a='center',f='none',st='none',lw=0){const q=sh('rect',x,y,w,h,f,st,lw);q.name=`文字-${seq}-${t.replaceAll('\n','')}`;q.text=t;q.text.style={typeface:'微软雅黑',fontSize:sz,color:c,bold:b,alignment:a,verticalAlignment:'middle',insets:0,wrap:'none',autoFit:'none'};return q}
function li(x1,y1,x2,y2,c=C.blue,lw=1.2,dash=false){const q=sh('line',Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1),'none',c,lw);q.position={left:Math.min(x1,x2),top:Math.min(y1,y2),width:Math.abs(x2-x1),height:Math.abs(y2-y1),verticalFlip:y2<y1};if(dash)q.line={fill:c,width:lw,style:'dash'};return q}
function ar(x,y,w=22,h=32){return sh('rightArrow',x,y,w,h,{type:'gradient',gradientKind:'linear',angleDeg:0,stops:[{offset:0,color:'#A8C9EA'},{offset:100000,color:C.blue}]})}
function dn(x,y,w=20,h=16){return sh('downArrow',x,y,w,h,C.blue)}
function conn(x1,y1,x2,y2,c=C.blue,lw=1.2,dash=false){li(x1,y1,x2,y2,c,lw,dash);if(y1===y2)sh('triangle',x2-5,y2-4,8,8,c).position={left:x2-5,top:y2-4,width:8,height:8,rotation:90};else if(x1===x2)sh('triangle',x2-4,y2-5,8,8,c).position={left:x2-4,top:y2-5,width:8,height:8,rotation:180};}
function page(x,y,w,h){sh('rect',x+4,y-4,w,h,'#FFFFFF','#999999',.7);sh('rect',x,y,w,h,'#FFFFFF',C.stroke,1);}
function rows(x,y,w,count=5,gap=9,c='#A5ADB8'){for(let i=0;i<count;i++)li(x,y+i*gap,x+w*(i%3===2?.74:1),y+i*gap,c,.7)}
function grid(x,y,n,size,fill){for(let i=0;i<n;i++)for(let j=0;j<n;j++)sh('rect',x+j*size,y+i*size,size-1,size-1,fill(i,j),'#FFFFFF',.4)}
function vec(x,y,cols,size=11){const cs=[C.ice,C.lav,'#E6ECF8','#5F91C4'];for(let i=0;i<cols;i++)sh('rect',x+i*(size+2),y,size,size,cs[(i+Math.floor(y))%cs.length],C.stroke,.6)}

// Preserve the reference canvas and framing; source objects replace the first frames after export.
sh('rect',0,0,1280,75,'#0854A0');sh('rect',50,118,1180,203,C.pale);
sh('rect',58,123,230,191,'#FFFFFF',C.blue,1.6);sh('rect',310,123,423,191,'#FFFFFF',C.blue,1.6);sh('rect',757,124,422,190,'#FFFFFF',C.blue,1.6);
ar(290,184,20,53);ar(735,184,20,53);

// Problem 1: actual document structure, not generic file icons.
tx('长文档与多格式材料',68,131,210,35,21,C.ink,true,'center',C.soft);
page(80,184,80,101);page(183,184,80,101);
tx('报告正文',83,188,74,14,12,C.ink,true);
sh('rect',87,207,65,12,'#D9E9FA',C.blue,.7);rows(91,211,52,1);
sh('rect',87,225,65,31,'#E6E2F8','#7770A4',.7);rows(91,231,51,3,8);
sh('rect',87,262,65,17,'#FFFFFF',C.red,1.2);rows(91,267,50,1);
tx('预算附表',186,188,74,14,12,C.ink,true);
for(let i=0;i<6;i++)for(let j=0;j<3;j++)sh('rect',190+j*21,207+i*11,21,11,i===0?'#BED4EE':'#FFFFFF','#8796AD',.6);
sh('rect',190,262,63,11,'none',C.red,1.2);
ar(161,220,20,24);tx('章节 / 表格 / 扫描附件',70,169,206,17,14,C.blue,true);
tx('版面异构，材料缺口难定位',68,289,210,22,16,C.red,true);

// Problem 2: many-to-many semantic links with a schematic matching matrix.
tx('跨章节语义关联复杂',321,131,400,35,22,C.ink,true,'center',C.soft);
tx('建设内容',330,169,111,18,14,C.blue,true);tx('功能清单',477,169,111,18,14,C.blue,true);
const left=['用户管理','数据共享','统一认证','业务办理'],right=['账户维护','信息交换','身份认证','统计分析'];
const ys=[194,219,244,269];
[[0,0],[0,2],[1,1],[1,3],[2,0],[2,2]].forEach(([i,j])=>li(441,ys[i]+10,477,ys[j]+10,i===j?C.blue:'#A6B8CD',i===j?1.5:.9));
left.forEach((t,i)=>tx(t,330,ys[i],111,20,14,C.ink,false,'center',i%2?C.lav:C.ice,C.stroke,.8));
right.forEach((t,i)=>tx(t,477,ys[i],111,20,14,C.ink,false,'center',i%2?C.ice:C.lav,C.stroke,.8));
li(441,279,466,279,C.red,1.6,true);tx('×',458,264,18,23,20,C.red,true);
tx('关系矩阵',620,175,90,17,13,C.ink,true);grid(626,199,4,19,(i,j)=>i===3?'#EDF1F7':i===j?'#3979B8':(i+j)%3?'#D7E4F4':'#96B9DF');
sh('rect',625,256,76,19,'none',C.red,1.3);tx('未匹配项',620,278,89,17,13,C.red,true);
tx('语义改写、跨表对应与功能重复',321,291,401,20,17,C.red,true);

// Problem 3: scattered policy and source evidence with ambiguous applicability.
tx('审查依据分散，结论难回溯',768,132,399,35,21,C.ink,true,'center',C.soft);
['市级口径','区级口径','政策版本'].forEach((t,i)=>tx(t,777+i*124,176,111,21,14,C.blue,true,'center',C.soft,C.blue,.8));
['规则 R1','规则 R2','条款 R3'].forEach((t,i)=>{sh('ellipse',794+i*124,210,76,27,i===2?C.lav:C.ice,C.stroke,.9);tx(t,794+i*124,210,76,27,14);});
li(832,237,958,251,C.blue,1);li(956,237,958,251,C.blue,1);li(1080,237,958,251,C.blue,1);
tx('审查结论：某功能疑似重复',793,251,337,25,16,C.ink,true,'center','#FFFFFF',C.stroke,.9);
tx('原文片段  ？   适用规则  ？   复核记录  ？',773,283,390,20,15,C.red,true);
tx('审\n查\n难',1176,126,45,188,24,'#FFFFFF',true,'center',C.blue);

// Core statement matches the reference's full blue central band.
tx('融合文档结构与语义关系，以规则和证据支撑可追溯的智能辅助审查',50,325,1180,64,29,'#FFFFFF',true,'center',C.blue);
sh('rect',50,397,1180,284,C.pale);tx('总\n体\n思\n想',50,397,69,284,29,'#FFFFFF',true,'center',C.blue);
sh('rect',135,405,1086,264,'#FFFFFF','#D9D9D9',1.5);

// [1] Adaptation of LayoutLMv3's text + 2-D layout representation principle.
tx('文档结构化与版面融合 [1]',147,414,301,29,21,C.ink,true);
page(157,457,90,132);tx('可研报告',161,461,81,15,12,C.ink,true);
sh('rect',166,481,71,14,'#D7E8FA',C.blue,.8);rows(171,486,57,1);
sh('rect',166,502,71,33,'#E7E1F7','#7770A4',.8);rows(171,508,57,3,9);
for(let i=0;i<3;i++)for(let j=0;j<3;j++)sh('rect',166+j*23.5,542+i*12,23.5,12,i===0?C.ice:'#FFFFFF','#8297B2',.6);
li(238,488,262,488,C.blue,1);li(238,519,262,531,C.blue,1);li(238,553,262,574,C.blue,1);
tx('文本词向量',266,456,173,21,15,C.blue,true,'left');vec(269,480,8,13);
tx('二维版面位置',266,507,173,21,15,C.blue,true,'left');vec(269,531,8,13);
tx('章节 / 表格索引',266,552,173,21,15,C.blue,true,'left');vec(269,576,8,13);
li(318,494,318,604,C.blue,1.2);li(357,545,357,604,C.blue,1.2);li(396,590,396,604,C.blue,1.2);
li(202,589,202,604,C.blue,1.2);li(202,604,396,604,C.blue,1.2);dn(291,604,20,14);
tx('文本 + 版面位置联合表示',153,620,292,28,17,'#FFFFFF',true,'center',C.blue);
tx('章节、表格、功能点与原文坐标',148,650,300,17,13,C.ink);
ar(451,540,22,37);

// [2] SBERT-style tied encoders + pooling + cosine similarity; scores are schematic.
tx('语义匹配与跨章节关联 [2]',477,414,349,29,21,C.ink,true);
tx('建设内容片段',486,455,144,23,16,C.ink,false,'center',C.soft,C.stroke,.8);
tx('功能清单片段',666,455,144,23,16,C.ink,false,'center',C.soft,C.stroke,.8);
dn(548,480,16,11);dn(729,480,16,11);
[492,672].forEach(x=>{for(let i=2;i>=0;i--)sh('rect',x+3*i,496-3*i,128,29,i===0?C.ice:'#E5ECF6',C.stroke,.8);tx('语义编码器',x,497,128,27,17,C.ink,true);dn(x+57,528,16,10);tx('Pooling',x,540,128,19,14,C.ink,false,'center',C.lav,C.stroke,.8);vec(x+10,566,8,10)});
tx('共\n享\n权\n重',633,491,30,42,10,C.blue,true);
li(556,577,556,586);li(736,577,736,586);li(556,586,736,586);dn(638,586,16,11);
tx('Sᵢⱼ = cos(hᵢ, hⱼ)',554,599,185,22,18,C.blue,true,'center','#FFFFFF',C.stroke,.8);
grid(491,627,4,9,(i,j)=>i===3?'#EBEFF7':i===j?'#3979B8':(i+j)%2?'#C0D4EE':'#82AAD4');sh('rect',490,653,36,9,'none',C.red,1);
tx('示意',530,639,39,19,12,C.ink);
tx('疑似重复候选 / 对应关系缺口',575,627,244,20,15,C.red,true);
tx('结合规则与上下文复核',575,649,244,17,13,C.ink);
ar(835,540,24,37);

// [3] FEVER-inspired retrieval -> evidence selection -> verification with uncertainty.
tx('证据检索与结论核验 [3]',869,414,336,29,21,C.ink,true);
sh('can',880,451,143,42,C.soft,C.stroke,1);
tx('规则 / 政策库',882,460,139,23,16,C.ink,true);
tx('待核验事项',1042,454,153,34,17,C.ink,true,'center',C.lav,C.stroke,1);
li(951,492,951,502);li(1118,488,1118,502);li(951,502,1118,502);dn(1027,502,17,12);
tx('证据检索与筛选',936,518,200,26,17,'#FFFFFF',true,'center',C.blue);
dn(1027,547,17,10);
['[e1] 报告原文及所在章节','[e2] 政策条款及适用范围','[e3] 关联附表与对应功能'].forEach((t,i)=>tx(t,888+3*(2-i),559+i*18,299,19,13,C.ink,false,'left',i%2?C.lav:C.soft,C.stroke,.6));
dn(1027,615,17,10);tx('规则校验 + 语义核验',896,628,286,24,17,'#FFFFFF',true,'center',C.blue);
tx('证据支持',878,655,101,20,14,C.blue,true,'center','#DFEAF7',C.blue,.6);
tx('发现冲突',985,655,101,20,14,C.red,true,'center','#F6E5E5',C.red,.6);
tx('材料不足',1092,655,105,20,14,C.ink,true,'center',C.soft,'#999999',.6);

// Reference-style footnote, with accurate method-borrowing disclosure.
tx('■  方法图依据论文改绘，匹配矩阵为示意；审查结果保留原文与规则依据，并经人工复核',58,685,1155,17,13,C.ink,false,'left');
tx('[1] Huang et al., LayoutLMv3, ACM MM 2022.   [2] Reimers & Gurevych, Sentence-BERT, EMNLP 2019.   [3] Thorne et al., FEVER, NAACL 2018.',58,704,1160,13,10.8,C.ink,false,'left');
tx('1',1234,680,20,25,18,'#888888');
s.speakerNotes.text=`内容依据：20261008-项目文档_修改版(3).docx。风格参考原PPT第1、2页。页面为总体设计示意，并非已部署模型清单，不表示项目已采用下列特定模型，也不借用论文性能作为项目成效。
[1] Yupan Huang, Tengchao Lv, Lei Cui, Yutong Lu, Furu Wei. LayoutLMv3: Pre-training for Document AI with Unified Text and Image Masking. ACM MM 2022. DOI: 10.1145/3503161.3548112. https://arxiv.org/abs/2204.08387 。图3借鉴文本与二维版面联合表示思想，删除预训练目标，改绘为可研文档解析方法示意。扫描材料识别仍属于后续探索。
[2] Nils Reimers, Iryna Gurevych. Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks. EMNLP-IJCNLP 2019. DOI: 10.18653/v1/D19-1410. https://aclanthology.org/D19-1410/ 。图2借鉴共享编码、池化与余弦相似度流程。功能对应与重复检查属于业务适配，需规则与上下文复核，不能仅凭相似度认定。匹配矩阵颜色为示意，不是实测数据。
[3] James Thorne, Andreas Vlachos, Christos Christodoulopoulos, Arpit Mittal. FEVER: a Large-scale Dataset for Fact Extraction and VERification. NAACL 2018. DOI: 10.18653/v1/N18-1074. https://aclanthology.org/N18-1074/ 。借鉴证据检索、句子筛选、核验和资料不足输出的流程思想，改绘为政策与可研报告审查示意，非直接复用FEVER数据或分类器。材料不足需人工确认，完整性通过不代表专项审查通过。`;
await(await PresentationFile.exportPptx(p)).save(`${dir}/revised-generated.pptx`);
console.log('Revised slide exported with editable adapted method diagrams');
