import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
process.env.RUNTIME_NODE_MODULES='C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const skill='C:/Users/ASUS/.codex/plugins/cache/openai-primary-runtime/presentations/26.1009.11318/skills/presentations';
const { finalizePresentation } = await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
const reference='C:/Users/ASUS/Desktop/20251211-数智原生、服务智联：面向未来的PaaS云平台-单独新增部分补数据.pptx';
const finalPath='D:/AIassist/output/AI助手-现状问题与总体思想-论文借鉴版.pptx';
await fs.mkdir(path.dirname(finalPath),{recursive:true});
const result=await finalizePresentation({
 workspaceDir:'D:/AIassist',
 candidatePath:'D:/AIassist/.codex-run/ai-assistant-slide-design/revised-candidate.pptx',
 finalPath,
 explicitTotalSlideCount:1,
 pythonExecutable:'C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',
 integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit'],
 requiredNativeTableOwnerSlides:[],
 requiredNativeChartOwnerSlides:[],
 fontPolicy:{basis:'reference',families:['微软雅黑'],referencePath:reference,referenceSha256:createHash('sha256').update(await fs.readFile(reference)).digest('hex')},
 verifyArtifactToolImport:true,
 receiptPath:'D:/AIassist/.codex-run/ai-assistant-slide-design/revised-validation.json'
});
console.log(JSON.stringify(result));

