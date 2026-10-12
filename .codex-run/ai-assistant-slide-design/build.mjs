import fs from 'node:fs/promises';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const dir = 'D:/AIassist/.codex-run/ai-assistant-slide-design';
const p = Presentation.create({ slideSize: { width: 1280, height: 720 } });
const s = p.slides.add();
s.background.fill = '#FFFFFF';
const C = { blue:'#155DA5', header:'#0B55A2', pale:'#C9DAF0', soft:'#F2F2F2', ink:'#000000', red:'#A53533', stroke:'#101843', lavender:'#CFD7FF', ice:'#8DC0FF' };
let seq=0;
function shape(g,x,y,w,h,fill='none',stroke='none',lw=0,name='') {
 return s.shapes.add({geometry:g,name:name||`图形-${++seq}`,position:{left:x,top:y,width:w,height:h},fill,line:{fill:stroke,width:lw,style:'solid'}});
}
function txt(t,x,y,w,h,size=18,color=C.ink,bold=false,align='center',fill='none',stroke='none',lw=0) {
 const a=shape('rect',x,y,w,h,fill,stroke,lw,`文字-${++seq}-${t.replace(/\n/g,'')}`);
 a.text=t; a.text.style={typeface:'微软雅黑',fontSize:size,color,bold,alignment:align,verticalAlignment:'middle',wrap:'none',autoFit:'none',insets:0};
 return a;
}
function line(x1,y1,x2,y2,color=C.blue,lw=1.5,dash=false) {
 const q=shape('line',Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1),'none',color,lw);
 q.position={left:Math.min(x1,x2),top:Math.min(y1,y2),width:Math.abs(x2-x1),height:Math.abs(y2-y1),verticalFlip:y2<y1};
 if(dash)q.line={fill:color,width:lw,style:'dash'};
 return q;
}
function arrow(x,y,w=21,h=35){return shape('rightArrow',x,y,w,h,{type:'gradient',gradientKind:'linear',angleDeg:0,stops:[{offset:0,color:'#A8C9EA'},{offset:100000,color:C.blue}]});}
function down(x,y,w=25,h=20){return shape('downArrow',x,y,w,h,C.blue);}
function doc(x,y,w,h,label,color=C.blue) {
 s.shapes.add({geometry:'custom',name:`文档-${++seq}`,position:{left:x,top:y,width:w,height:h},fill:'#FFFFFF',line:{fill:color,width:1.2,style:'solid'},customPaths:[{width:w,height:h,commands:[{moveTo:{x:0,y:0}},{lineTo:{x:w-11,y:0}},{lineTo:{x:w,y:11}},{lineTo:{x:w,y:h}},{lineTo:{x:0,y:h}},{close:{}}]}]});
 line(x+w-11,y,x+w-11,y+11,color,1);line(x+w-11,y+11,x+w,y+11,color,1);
 txt(label,x+3,y+12,w-6,h-24,15,color,true);
}

// Template-aligned framing. Original title and outer frames are restored after export.
shape('rect',0,0,1280,75,C.header);
shape('rect',50,118,1180,203,C.pale);
shape('rect',58,123,230,191,'#FFFFFF',C.blue,1.6);
shape('rect',310,123,423,191,'#FFFFFF',C.blue,1.6);
shape('rect',757,124,422,190,'#FFFFFF',C.blue,1.6);
arrow(290,184,20,53);arrow(735,184,20,53);

// Current review practice: multi-material input and manual comparison.
txt('多材料人工审查',68,131,210,35,22,C.ink,true,'center',C.soft);
txt('正文、表格与附件分散',67,171,213,24,17,C.ink,true);
doc(72,201,58,58,'可研\n正文');doc(141,201,58,58,'预算\n表格');doc(210,201,58,58,'政策\n附件');
line(101,260,101,269);line(170,260,170,269);line(239,260,239,269);line(101,269,239,269);down(158,269,24,13);
txt('逐项翻阅、人工比对',77,282,197,24,18,C.red,true);

// Missing links and duplicate functions.
txt('跨章节对应关系复杂',321,131,400,35,22,C.ink,true,'center',C.soft);
const xs=[323,404,485,566,647];
const labs=['建设依据','建设内容','业务需求','系统功能','功能清单'];
labs.forEach((t,i)=>txt(t,xs[i],190,70,42,16,C.stroke,false,'center',i%2?C.lavender:C.ice,C.stroke,1));
for(let i=0;i<4;i++) {
 if(i===2){line(xs[i]+70,211,xs[i+1],211,C.red,1.7,true);txt('×',550,190,20,29,22,C.red,true);}
 else arrow(xs[i]+72,204,7,14);
}
txt('用户管理',336,254,117,28,16,C.ink,false,'center','#F6F6F6',C.red,1.3);
txt('用户管理',466,254,117,28,16,C.ink,false,'center','#F6F6F6',C.red,1.3);
txt('疑似重复',595,254,114,28,17,C.red,true);
txt('关联核对量大，缺项与重复难发现',322,289,400,23,17.5,C.red,true);

// Scattered rule sources and unsupported review opinions.
txt('依据分散，结果难追溯',768,132,399,35,22,C.ink,true,'center',C.soft);
doc(777,190,64,60,'市级\n规则');doc(852,190,64,60,'区级\n规则');doc(927,190,64,60,'政策\n文件');
arrow(997,208,20,25);
txt('审查意见',1025,188,132,31,19,C.ink,true,'center',C.soft,C.stroke,1);
txt('问题描述\n修改意见',1025,219,132,40,16,C.ink,false,'center','#FFFFFF',C.stroke,1);
txt('原文位置  ？    命中规则  ？',779,270,377,25,17,C.red,true);
txt('审查口径难统一，复核留痕不足',769,291,398,22,17.5,C.red,true);
txt('审\n查\n难',1176,126,45,188,24,'#FFFFFF',true,'center',C.blue);

// Central statement uses the exact blue banner position and type hierarchy.
txt('统一规则牵引分项检查，以原文证据支撑可追溯、可复核的辅助审查',50,325,1180,64,29,'#FFFFFF',true,'center',C.blue);
shape('rect',50,397,1180,284,C.pale);
txt('总\n体\n思\n想',50,397,69,284,29,'#FFFFFF',true,'center',C.blue);
shape('rect',135,405,1086,264,'#FFFFFF','#D9D9D9',1.5);

// Native editable architecture diagram.
txt('规则驱动与智能体协同检查',149,414,676,31,24,C.ink,true);
txt('原文证据与人工复核',874,414,333,31,24,C.ink,true);
txt('规则库网站',152,457,173,28,20,'#FFFFFF',true,'center',C.blue,C.stroke,1);
txt('市级 / 区级判断标准\n审核、版本与操作日志',152,485,173,43,15,C.ink,false,'center','#FFFFFF',C.stroke,1);
line(325,493,481,493,C.blue,2);arrow(472,485,18,17);
txt('统一规则调用',335,465,144,23,16,C.blue,true);

txt('桌面端应用',152,572,173,27,20,'#FFFFFF',true,'center',C.blue,C.stroke,1);
txt('报告上传、任务配置',152,599,173,33,15,C.ink,false,'center','#FFFFFF',C.stroke,1);
arrow(327,593,22,22);
txt('文档结构化',352,572,112,27,17,C.ink,true,'center',C.lavender,C.stroke,1);
txt('章节 / 表格\n功能点提取',352,599,112,33,14,C.ink,false,'center','#FFFFFF',C.stroke,1);
arrow(466,593,23,22);

shape('rect',491,455,337,177,'#FFFFFF',C.stroke,1.4);
txt('专项检查智能体',491,455,337,31,21,'#FFFFFF',true,'center',C.blue);
txt('完整性检查',502,495,315,29,17,C.ink,true,'center',C.soft,C.stroke,1);
[['建设依据审查',502,534],['重复建设检查',665,534],['功能对应关系',502,570],['敏感词检查',665,570]].forEach(([t,x,y])=>txt(t,x,y,152,28,17,C.ink,false,'center',C.lavender,C.stroke,1));
txt('规则校验  +  大模型语义复核',502,606,315,21,17,C.blue,true);
txt('检测能力按业务需求逐步扩展',152,640,677,23,15,C.blue,false);

arrow(834,525,36,42);
// Example evidence is visibly labeled, avoiding claims about a real document.
shape('rect',880,457,323,160,'#FFFFFF',C.stroke,1.4);
txt('示例：功能清单缺少对应项',890,465,303,28,18,C.red,true,'center',C.soft);
txt('原文',891,504,53,26,16,'#FFFFFF',true,'center',C.blue);
txt('建设内容列出“用户管理”',953,504,238,26,16,C.ink,false,'left');
txt('规则',891,543,53,26,16,'#FFFFFF',true,'center',C.blue);
txt('建设内容与功能清单应对应',953,543,238,26,16,C.ink,false,'left');
txt('建议',891,582,53,26,16,'#FFFFFF',true,'center',C.blue);
txt('补充功能项，核实对应关系',953,582,238,26,16,C.ink,false,'left');
txt('人工复核',882,635,137,29,18,C.ink,true,'center',C.ice,C.stroke,1.2);
arrow(1023,640,20,20);
txt('结果导出与留痕',1047,635,155,29,18,C.ink,true,'center',C.lavender,C.stroke,1.2);
txt('■  辅助检查提供问题线索与判断依据，审查结论经人工复核确认',58,687,1130,23,15,C.ink,false,'left');
txt('1',1234,680,20,25,18,'#888888',false,'center');

s.speakerNotes.text='内容来源：20261008-项目文档_修改版(3).docx。风格参考：20251211-数智原生、服务智联：面向未来的PaaS云平台-单独新增部分补数据.pptx，第1、2页。页面展示当前可研报告审查痛点和系统总体设计，不宣称所有能力已完成。证据示例为示意，非真实报告检测结果。完整性通过仅表示未发现明显材料残缺，不代表专项审查通过。';
await (await PresentationFile.exportPptx(p)).save(`${dir}/generated.pptx`);
console.log('Exported editable slide');
