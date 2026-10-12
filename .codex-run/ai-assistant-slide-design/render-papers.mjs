import fs from 'node:fs/promises';
import {createCanvas,DOMMatrix,Path2D,ImageData} from '@napi-rs/canvas';
globalThis.DOMMatrix=DOMMatrix;globalThis.Path2D=Path2D;globalThis.ImageData=ImageData;
const pdfjs=await import('pdfjs-dist/legacy/build/pdf.mjs');
const dir='D:/AIassist/.codex-run/ai-assistant-slide-design/papers';
for(const [name,num] of [['layoutlmv3',3],['sbert',3],['fever',2]]){
 const pdf=await pdfjs.getDocument({data:new Uint8Array(await fs.readFile(`${dir}/${name}.pdf`)),disableFontFace:true,useSystemFonts:true}).promise;
 const page=await pdf.getPage(num);const viewport=page.getViewport({scale:1.5});
 const canvas=createCanvas(Math.ceil(viewport.width),Math.ceil(viewport.height));
 await page.render({canvasContext:canvas.getContext('2d'),viewport,canvas}).promise;
 await fs.writeFile(`${dir}/${name}-page.png`,canvas.toBuffer('image/png'));
 console.log(`${name}: ${viewport.width}x${viewport.height}`);
}
