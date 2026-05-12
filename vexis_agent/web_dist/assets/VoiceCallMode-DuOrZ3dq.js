var g2=Object.defineProperty;var y2=(e,r,a)=>r in e?g2(e,r,{enumerable:!0,configurable:!0,writable:!0,value:a}):e[r]=a;var Um=(e,r,a)=>y2(e,typeof r!="symbol"?r+"":r,a);import{r as ii,a as Lm,A as _2,j as Vr}from"./index-DjmNl2zI.js";var ad={},$o={},qm;function Dw(){if(qm)return $o;qm=1,Object.defineProperty($o,"__esModule",{value:!0}),$o.baseAssetPath=void 0;const r=typeof window<"u"&&typeof window.document<"u"?window.document.currentScript:null;let a="/";return r&&(a=r.src.replace(/#.*$/,"").replace(/\?.*$/,"").replace(/\/[^/]+$/,"/")),$o.baseAssetPath=a,$o}var vo={},Vm;function bp(){if(Vm)return vo;Vm=1,Object.defineProperty(vo,"__esModule",{value:!0}),vo.defaultModelFetcher=void 0;const e=r=>fetch(r).then(a=>a.arrayBuffer());return vo.defaultModelFetcher=e,vo}var ba={},xo={},Wm;function Ho(){if(Wm)return xo;Wm=1,Object.defineProperty(xo,"__esModule",{value:!0}),xo.log=void 0;const e=r=>a=>{console.log(`VAD | ${r} >`,a)};return xo.log={error:e("error"),debug:e("debug"),warn:e("warn")},xo}var So={},Gm;function ll(){if(Gm)return So;Gm=1,Object.defineProperty(So,"__esModule",{value:!0}),So.Message=void 0;var e;return(function(r){r.AudioFrame="AUDIO_FRAME",r.SpeechStart="SPEECH_START",r.VADMisfire="VAD_MISFIRE",r.SpeechEnd="SPEECH_END",r.SpeechStop="SPEECH_STOP",r.SpeechRealStart="SPEECH_REAL_START",r.FrameProcessed="FRAME_PROCESSED"})(e||(So.Message=e={})),So}var Fm;function $p(){if(Fm)return ba;Fm=1,Object.defineProperty(ba,"__esModule",{value:!0}),ba.FrameProcessor=ba.validateOptions=ba.defaultFrameProcessorOptions=void 0;const e=Ho(),r=ll();ba.defaultFrameProcessorOptions={positiveSpeechThreshold:.3,negativeSpeechThreshold:.25,preSpeechPadMs:800,redemptionMs:1400,minSpeechMs:400,submitUserSpeechOnPause:!1};function a(d){(d.positiveSpeechThreshold<0||d.positiveSpeechThreshold>1)&&e.log.error("positiveSpeechThreshold should be a number between 0 and 1"),(d.negativeSpeechThreshold<0||d.negativeSpeechThreshold>d.positiveSpeechThreshold)&&e.log.error("negativeSpeechThreshold should be between 0 and positiveSpeechThreshold"),d.preSpeechPadMs<0&&e.log.error("preSpeechPadMs should be positive"),d.redemptionMs<0&&e.log.error("redemptionMs should be positive"),d.minSpeechMs<0&&e.log.error("minSpeechMs should be positive")}ba.validateOptions=a;const s=d=>{const g=d.reduce((_,v)=>(_.push(_.at(-1)+v.length),_),[0]),m=new Float32Array(g.at(-1));return d.forEach((_,v)=>{const x=g[v];m.set(_,x)}),m};function o(d,g){const m=Math.floor(d.redemptionMs/g),_=Math.floor(d.preSpeechPadMs/g),v=Math.floor(d.minSpeechMs/g);return{redemptionFrames:m,preSpeechPadFrames:_,minSpeechFrames:v}}class p{constructor(g,m,_,v){this.modelProcessFunc=g,this.modelResetFunc=m,this.options=_,this.msPerFrame=v,this.speaking=!1,this.redemptionCounter=0,this.speechFrameCount=0,this.active=!1,this.speechRealStartFired=!1,this.setOptions=A=>{this.options={...this.options,...A};const{redemptionFrames:R,preSpeechPadFrames:H,minSpeechFrames:U}=o(this.options,this.msPerFrame);this.redemptionFrames=R,this.preSpeechPadFrames=H,this.minSpeechFrames=U},this.reset=()=>{this.speaking=!1,this.speechRealStartFired=!1,this.audioBuffer=[],this.modelResetFunc(),this.redemptionCounter=0,this.speechFrameCount=0},this.pause=A=>{this.active=!1,this.options.submitUserSpeechOnPause?this.endSegment(A):this.reset()},this.resume=()=>{this.active=!0},this.endSegment=A=>{const R=this.audioBuffer;this.audioBuffer=[];const H=this.speaking;if(this.reset(),H)if(R.reduce((P,F)=>F.isSpeech?P+1:P,0)>=this.minSpeechFrames){const P=s(R.map(F=>F.frame));A({msg:r.Message.SpeechEnd,audio:P})}else A({msg:r.Message.VADMisfire});return{}},this.process=async(A,R)=>{if(!this.active)return;const H=await this.modelProcessFunc(A),U=H.isSpeech>=this.options.positiveSpeechThreshold;if(R({probs:H,msg:r.Message.FrameProcessed,frame:A}),this.audioBuffer.push({frame:A,isSpeech:U}),U&&(this.speechFrameCount++,this.redemptionCounter=0),U&&!this.speaking&&(this.speaking=!0,R({msg:r.Message.SpeechStart})),this.speaking&&this.speechFrameCount===this.minSpeechFrames&&!this.speechRealStartFired&&(this.speechRealStartFired=!0,R({msg:r.Message.SpeechRealStart})),H.isSpeech<this.options.negativeSpeechThreshold&&this.speaking&&++this.redemptionCounter>=this.redemptionFrames){this.redemptionCounter=0,this.speechFrameCount=0,this.speaking=!1,this.speechRealStartFired=!1;const P=this.audioBuffer;if(this.audioBuffer=[],P.reduce((G,K)=>K.isSpeech?G+1:G,0)>=this.minSpeechFrames){const G=s(P.map(K=>K.frame));R({msg:r.Message.SpeechEnd,audio:G})}else R({msg:r.Message.VADMisfire})}if(!this.speaking){for(;this.audioBuffer.length>this.preSpeechPadFrames;)this.audioBuffer.shift();this.speechFrameCount=0}},this.audioBuffer=[];const{redemptionFrames:x,preSpeechPadFrames:T,minSpeechFrames:C}=o(this.options,this.msPerFrame);this.redemptionFrames=x,this.preSpeechPadFrames=T,this.minSpeechFrames=C,this.reset()}}return ba.FrameProcessor=p,ba}var $a={};function wi(e){throw new Error('Could not dynamically require "'+e+'". Please configure the dynamicRequireTargets or/and ignoreDynamicRequires option of @rollup/plugin-commonjs appropriately for this require call to work.')}var nd={exports:{}};/*!
 * ONNX Runtime Web v1.26.0
 * Copyright (c) Microsoft Corporation. All rights reserved.
 * Licensed under the MIT License.
 */var Hm;function w2(){return Hm||(Hm=1,(function(e,r){var a=(()=>{var s=Object.defineProperty,o=Object.getOwnPropertyDescriptor,p=Object.getOwnPropertyNames,d=Object.prototype.hasOwnProperty,g=(t=>typeof wi<"u"?wi:typeof Proxy<"u"?new Proxy(t,{get:(i,n)=>(typeof wi<"u"?wi:i)[n]}):t)(function(t){if(typeof wi<"u")return wi.apply(this,arguments);throw Error('Dynamic require of "'+t+'" is not supported')}),m=(t,i)=>()=>(t&&(i=t(t=0)),i),_=(t,i)=>{for(var n in i)s(t,n,{get:i[n],enumerable:!0})},v=(t,i,n,u)=>{if(i&&typeof i=="object"||typeof i=="function")for(let l of p(i))!d.call(t,l)&&l!==n&&s(t,l,{get:()=>i[l],enumerable:!(u=o(i,l))||u.enumerable});return t},x=t=>v(s({},"__esModule",{value:!0}),t),T,C,A,R,H,U=m(()=>{T=new Map,C=[],A=(t,i,n)=>{if(i&&typeof i.init=="function"&&typeof i.createInferenceSessionHandler=="function"){let u=T.get(t);if(u===void 0)T.set(t,{backend:i,priority:n});else{if(u.priority>n)return;if(u.priority===n&&u.backend!==i)throw new Error(`cannot register backend "${t}" using priority ${n}`)}if(n>=0){let l=C.indexOf(t);l!==-1&&C.splice(l,1);for(let c=0;c<C.length;c++)if(T.get(C[c]).priority<=n){C.splice(c,0,t);return}C.push(t)}return}throw new TypeError("not a valid backend")},R=async t=>{let i=T.get(t);if(!i)return"backend not found.";if(i.initialized)return i.backend;if(i.aborted)return i.error;{let n=!!i.initPromise;try{return n||(i.initPromise=i.backend.init(t)),await i.initPromise,i.initialized=!0,i.backend}catch(u){return n||(i.error=`${u}`,i.aborted=!0),i.error}finally{delete i.initPromise}}},H=async t=>{let i=t.executionProviders||[],n=i.map(b=>typeof b=="string"?b:b.name),u=n.length===0?C:n,l,c=[],h=new Set;for(let b of u){let $=await R(b);typeof $=="string"?c.push({name:b,err:$}):(l||(l=$),l===$&&h.add(b))}if(!l)throw new Error(`no available backend found. ERR: ${c.map(b=>`[${b.name}] ${b.err}`).join(", ")}`);for(let{name:b,err:$}of c)n.includes(b)&&console.warn(`removing requested execution provider "${b}" from session options because it is not available: ${$}`);let y=i.filter(b=>h.has(typeof b=="string"?b:b.name));return[l,new Proxy(t,{get:(b,$)=>$==="executionProviders"?y:Reflect.get(b,$)})]}}),P=m(()=>{U()}),F,G=m(()=>{F="1.26.0"}),K,ee,ae=m(()=>{G(),K="warning",ee={wasm:{},webgl:{},webgpu:{},versions:{common:F},set logLevel(t){if(t!==void 0){if(typeof t!="string"||["verbose","info","warning","error","fatal"].indexOf(t)===-1)throw new Error(`Unsupported logging level: ${t}`);K=t}},get logLevel(){return K}},Object.defineProperty(ee,"logLevel",{enumerable:!0})}),B,me=m(()=>{ae(),B=ee}),_e,Re,Ue=m(()=>{_e=(t,i)=>{let n=typeof document<"u"?document.createElement("canvas"):new OffscreenCanvas(1,1);n.width=t.dims[3],n.height=t.dims[2];let u=n.getContext("2d");if(u!=null){let l,c;(i==null?void 0:i.tensorLayout)!==void 0&&i.tensorLayout==="NHWC"?(l=t.dims[2],c=t.dims[3]):(l=t.dims[3],c=t.dims[2]);let h=(i==null?void 0:i.format)!==void 0?i.format:"RGB",y=i==null?void 0:i.norm,b,$;y===void 0||y.mean===void 0?b=[255,255,255,255]:typeof y.mean=="number"?b=[y.mean,y.mean,y.mean,y.mean]:(b=[y.mean[0],y.mean[1],y.mean[2],0],y.mean[3]!==void 0&&(b[3]=y.mean[3])),y===void 0||y.bias===void 0?$=[0,0,0,0]:typeof y.bias=="number"?$=[y.bias,y.bias,y.bias,y.bias]:($=[y.bias[0],y.bias[1],y.bias[2],0],y.bias[3]!==void 0&&($[3]=y.bias[3]));let k=c*l,I=0,O=k,M=k*2,D=-1;h==="RGBA"?(I=0,O=k,M=k*2,D=k*3):h==="RGB"?(I=0,O=k,M=k*2):h==="RBG"&&(I=0,M=k,O=k*2);for(let L=0;L<c;L++)for(let Z=0;Z<l;Z++){let W=(t.data[I++]-$[0])*b[0],V=(t.data[O++]-$[1])*b[1],J=(t.data[M++]-$[2])*b[2],Y=D===-1?255:(t.data[D++]-$[3])*b[3];u.fillStyle="rgba("+W+","+V+","+J+","+Y+")",u.fillRect(Z,L,1,1)}if("toDataURL"in n)return n.toDataURL();throw new Error("toDataURL is not supported")}else throw new Error("Can not access image data")},Re=(t,i)=>{let n=typeof document<"u"?document.createElement("canvas").getContext("2d"):new OffscreenCanvas(1,1).getContext("2d"),u;if(n!=null){let l,c,h;(i==null?void 0:i.tensorLayout)!==void 0&&i.tensorLayout==="NHWC"?(l=t.dims[2],c=t.dims[1],h=t.dims[3]):(l=t.dims[3],c=t.dims[2],h=t.dims[1]);let y=i!==void 0&&i.format!==void 0?i.format:"RGB",b=i==null?void 0:i.norm,$,k;b===void 0||b.mean===void 0?$=[255,255,255,255]:typeof b.mean=="number"?$=[b.mean,b.mean,b.mean,b.mean]:($=[b.mean[0],b.mean[1],b.mean[2],255],b.mean[3]!==void 0&&($[3]=b.mean[3])),b===void 0||b.bias===void 0?k=[0,0,0,0]:typeof b.bias=="number"?k=[b.bias,b.bias,b.bias,b.bias]:(k=[b.bias[0],b.bias[1],b.bias[2],0],b.bias[3]!==void 0&&(k[3]=b.bias[3]));let I=c*l;if(i!==void 0&&(i.format!==void 0&&h===4&&i.format!=="RGBA"||h===3&&i.format!=="RGB"&&i.format!=="BGR"))throw new Error("Tensor format doesn't match input tensor dims");let O=4,M=0,D=1,L=2,Z=3,W=0,V=I,J=I*2,Y=-1;y==="RGBA"?(W=0,V=I,J=I*2,Y=I*3):y==="RGB"?(W=0,V=I,J=I*2):y==="RBG"&&(W=0,J=I,V=I*2),u=n.createImageData(l,c);for(let se=0;se<c*l;M+=O,D+=O,L+=O,Z+=O,se++)u.data[M]=(t.data[W++]-k[0])*$[0],u.data[D]=(t.data[V++]-k[1])*$[1],u.data[L]=(t.data[J++]-k[2])*$[2],u.data[Z]=Y===-1?255:(t.data[Y++]-k[3])*$[3]}else throw new Error("Can not access image data");return u}}),Me,pe,qe,Ve,ze,ht,Ce=m(()=>{er(),Me=(t,i)=>{if(t===void 0)throw new Error("Image buffer must be defined");if(i.height===void 0||i.width===void 0)throw new Error("Image height and width must be defined");if(i.tensorLayout==="NHWC")throw new Error("NHWC Tensor layout is not supported yet");let{height:n,width:u}=i,l=i.norm??{mean:255,bias:0},c,h;typeof l.mean=="number"?c=[l.mean,l.mean,l.mean,l.mean]:c=[l.mean[0],l.mean[1],l.mean[2],l.mean[3]??255],typeof l.bias=="number"?h=[l.bias,l.bias,l.bias,l.bias]:h=[l.bias[0],l.bias[1],l.bias[2],l.bias[3]??0];let y=i.format!==void 0?i.format:"RGBA",b=i.tensorFormat!==void 0&&i.tensorFormat!==void 0?i.tensorFormat:"RGB",$=n*u,k=b==="RGBA"?new Float32Array($*4):new Float32Array($*3),I=4,O=0,M=1,D=2,L=3,Z=0,W=$,V=$*2,J=-1;y==="RGB"&&(I=3,O=0,M=1,D=2,L=-1),b==="RGBA"?J=$*3:b==="RBG"?(Z=0,V=$,W=$*2):b==="BGR"&&(V=0,W=$,Z=$*2);for(let Y=0;Y<$;Y++,O+=I,D+=I,M+=I,L+=I)k[Z++]=(t[O]+h[0])/c[0],k[W++]=(t[M]+h[1])/c[1],k[V++]=(t[D]+h[2])/c[2],J!==-1&&L!==-1&&(k[J++]=(t[L]+h[3])/c[3]);return b==="RGBA"?new _t("float32",k,[1,4,n,u]):new _t("float32",k,[1,3,n,u])},pe=async(t,i)=>{let n=typeof HTMLImageElement<"u"&&t instanceof HTMLImageElement,u=typeof ImageData<"u"&&t instanceof ImageData,l=typeof ImageBitmap<"u"&&t instanceof ImageBitmap,c=typeof t=="string",h,y=i??{},b=()=>{if(typeof document<"u")return document.createElement("canvas");if(typeof OffscreenCanvas<"u")return new OffscreenCanvas(1,1);throw new Error("Canvas is not supported")},$=k=>typeof HTMLCanvasElement<"u"&&k instanceof HTMLCanvasElement||k instanceof OffscreenCanvas?k.getContext("2d"):null;if(n){let k=b();k.width=t.width,k.height=t.height;let I=$(k);if(I!=null){let O=t.height,M=t.width;if(i!==void 0&&i.resizedHeight!==void 0&&i.resizedWidth!==void 0&&(O=i.resizedHeight,M=i.resizedWidth),i!==void 0){if(y=i,i.tensorFormat!==void 0)throw new Error("Image input config format must be RGBA for HTMLImageElement");y.tensorFormat="RGBA",y.height=O,y.width=M}else y.tensorFormat="RGBA",y.height=O,y.width=M;I.drawImage(t,0,0),h=I.getImageData(0,0,M,O).data}else throw new Error("Can not access image data")}else if(u){let k,I;if(i!==void 0&&i.resizedWidth!==void 0&&i.resizedHeight!==void 0?(k=i.resizedHeight,I=i.resizedWidth):(k=t.height,I=t.width),i!==void 0&&(y=i),y.format="RGBA",y.height=k,y.width=I,i!==void 0){let O=b();O.width=I,O.height=k;let M=$(O);if(M!=null)M.putImageData(t,0,0),h=M.getImageData(0,0,I,k).data;else throw new Error("Can not access image data")}else h=t.data}else if(l){if(i===void 0)throw new Error("Please provide image config with format for Imagebitmap");let k=b();k.width=t.width,k.height=t.height;let I=$(k);if(I!=null){let O=t.height,M=t.width;return I.drawImage(t,0,0,M,O),h=I.getImageData(0,0,M,O).data,y.height=O,y.width=M,Me(h,y)}else throw new Error("Can not access image data")}else{if(c)return new Promise((k,I)=>{let O=b(),M=$(O);if(!t||!M)return I();let D=new Image;D.crossOrigin="Anonymous",D.src=t,D.onload=()=>{O.width=D.width,O.height=D.height,M.drawImage(D,0,0,O.width,O.height);let L=M.getImageData(0,0,O.width,O.height);y.height=O.height,y.width=O.width,k(Me(L.data,y))}});throw new Error("Input data provided is not supported - aborted tensor creation")}if(h!==void 0)return Me(h,y);throw new Error("Input data provided is not supported - aborted tensor creation")},qe=(t,i)=>{let{width:n,height:u,download:l,dispose:c}=i,h=[1,u,n,4];return new _t({location:"texture",type:"float32",texture:t,dims:h,download:l,dispose:c})},Ve=(t,i)=>{let{dataType:n,dims:u,download:l,dispose:c}=i;return new _t({location:"gpu-buffer",type:n??"float32",gpuBuffer:t,dims:u,download:l,dispose:c})},ze=(t,i)=>{let{dataType:n,dims:u,download:l,dispose:c}=i;return new _t({location:"ml-tensor",type:n??"float32",mlTensor:t,dims:u,download:l,dispose:c})},ht=(t,i,n)=>new _t({location:"cpu-pinned",type:t,data:i,dims:n??[i.length]})}),nt,Te,Be,We,Ie=m(()=>{nt=new Map([["float32",Float32Array],["uint8",Uint8Array],["int8",Int8Array],["uint16",Uint16Array],["int16",Int16Array],["int32",Int32Array],["bool",Uint8Array],["float64",Float64Array],["uint32",Uint32Array],["int4",Uint8Array],["uint4",Uint8Array]]),Te=new Map([[Float32Array,"float32"],[Uint8Array,"uint8"],[Int8Array,"int8"],[Uint16Array,"uint16"],[Int16Array,"int16"],[Int32Array,"int32"],[Float64Array,"float64"],[Uint32Array,"uint32"]]),Be=!1,We=()=>{if(!Be){Be=!0;let t=typeof BigInt64Array<"u"&&BigInt64Array.from,i=typeof BigUint64Array<"u"&&BigUint64Array.from,n=globalThis.Float16Array,u=typeof n<"u"&&n.from;t&&(nt.set("int64",BigInt64Array),Te.set(BigInt64Array,"int64")),i&&(nt.set("uint64",BigUint64Array),Te.set(BigUint64Array,"uint64")),u?(nt.set("float16",n),Te.set(n,"float16")):nt.set("float16",Uint16Array)}}}),$t,_r,jt=m(()=>{er(),$t=t=>{let i=1;for(let n=0;n<t.length;n++){let u=t[n];if(typeof u!="number"||!Number.isSafeInteger(u))throw new TypeError(`dims[${n}] must be an integer, got: ${u}`);if(u<0)throw new RangeError(`dims[${n}] must be a non-negative integer, got: ${u}`);i*=u}return i},_r=(t,i)=>{switch(t.location){case"cpu":return new _t(t.type,t.data,i);case"cpu-pinned":return new _t({location:"cpu-pinned",data:t.data,type:t.type,dims:i});case"texture":return new _t({location:"texture",texture:t.texture,type:t.type,dims:i});case"gpu-buffer":return new _t({location:"gpu-buffer",gpuBuffer:t.gpuBuffer,type:t.type,dims:i});case"ml-tensor":return new _t({location:"ml-tensor",mlTensor:t.mlTensor,type:t.type,dims:i});default:throw new Error(`tensorReshape: tensor location ${t.location} is not supported`)}}}),_t,er=m(()=>{Ue(),Ce(),Ie(),jt(),_t=class{constructor(t,i,n){We();let u,l;if(typeof t=="object"&&"location"in t)switch(this.dataLocation=t.location,u=t.type,l=t.dims,t.location){case"cpu-pinned":{let h=nt.get(u);if(!h)throw new TypeError(`unsupported type "${u}" to create tensor from pinned buffer`);if(!(t.data instanceof h))throw new TypeError(`buffer should be of type ${h.name}`);this.cpuData=t.data;break}case"texture":{if(u!=="float32")throw new TypeError(`unsupported type "${u}" to create tensor from texture`);this.gpuTextureData=t.texture,this.downloader=t.download,this.disposer=t.dispose;break}case"gpu-buffer":{if(u!=="float32"&&u!=="float16"&&u!=="int32"&&u!=="int64"&&u!=="uint32"&&u!=="uint8"&&u!=="bool"&&u!=="uint4"&&u!=="int4")throw new TypeError(`unsupported type "${u}" to create tensor from gpu buffer`);this.gpuBufferData=t.gpuBuffer,this.downloader=t.download,this.disposer=t.dispose;break}case"ml-tensor":{if(u!=="float32"&&u!=="float16"&&u!=="int32"&&u!=="int64"&&u!=="uint32"&&u!=="uint64"&&u!=="int8"&&u!=="uint8"&&u!=="bool"&&u!=="uint4"&&u!=="int4")throw new TypeError(`unsupported type "${u}" to create tensor from MLTensor`);this.mlTensorData=t.mlTensor,this.downloader=t.download,this.disposer=t.dispose;break}default:throw new Error(`Tensor constructor: unsupported location '${this.dataLocation}'`)}else{let h,y;if(typeof t=="string")if(u=t,y=n,t==="string"){if(!Array.isArray(i))throw new TypeError("A string tensor's data must be a string array.");h=i}else{let b=nt.get(t);if(b===void 0)throw new TypeError(`Unsupported tensor type: ${t}.`);if(Array.isArray(i)){if(t==="float16"&&b===Uint16Array||t==="uint4"||t==="int4")throw new TypeError(`Creating a ${t} tensor from number array is not supported. Please use ${b.name} as data.`);t==="uint64"||t==="int64"?h=b.from(i,BigInt):h=b.from(i)}else if(i instanceof b)h=i;else if(i instanceof Uint8ClampedArray)if(t==="uint8")h=Uint8Array.from(i);else throw new TypeError("A Uint8ClampedArray tensor's data must be type of uint8");else if(t==="float16"&&i instanceof Uint16Array&&b!==Uint16Array)h=new globalThis.Float16Array(i.buffer,i.byteOffset,i.length);else throw new TypeError(`A ${u} tensor's data must be type of ${b}`)}else if(y=i,Array.isArray(t)){if(t.length===0)throw new TypeError("Tensor type cannot be inferred from an empty array.");let b=typeof t[0];if(b==="string")u="string",h=t;else if(b==="boolean")u="bool",h=Uint8Array.from(t);else throw new TypeError(`Invalid element type of data array: ${b}.`)}else if(t instanceof Uint8ClampedArray)u="uint8",h=Uint8Array.from(t);else{let b=Te.get(t.constructor);if(b===void 0)throw new TypeError(`Unsupported type for tensor data: ${t.constructor}.`);u=b,h=t}if(y===void 0)y=[h.length];else if(!Array.isArray(y))throw new TypeError("A tensor's dims must be a number array");l=y,this.cpuData=h,this.dataLocation="cpu"}let c=$t(l);if(this.cpuData&&c!==this.cpuData.length&&!((u==="uint4"||u==="int4")&&Math.ceil(c/2)===this.cpuData.length))throw new Error(`Tensor's size(${c}) does not match data length(${this.cpuData.length}).`);this.type=u,this.dims=l,this.size=c}static async fromImage(t,i){return pe(t,i)}static fromTexture(t,i){return qe(t,i)}static fromGpuBuffer(t,i){return Ve(t,i)}static fromMLTensor(t,i){return ze(t,i)}static fromPinnedBuffer(t,i,n){return ht(t,i,n)}toDataURL(t){return _e(this,t)}toImageData(t){return Re(this,t)}get data(){if(this.ensureValid(),!this.cpuData)throw new Error("The data is not on CPU. Use `getData()` to download GPU data to CPU, or use `texture` or `gpuBuffer` property to access the GPU data directly.");return this.cpuData}get location(){return this.dataLocation}get texture(){if(this.ensureValid(),!this.gpuTextureData)throw new Error("The data is not stored as a WebGL texture.");return this.gpuTextureData}get gpuBuffer(){if(this.ensureValid(),!this.gpuBufferData)throw new Error("The data is not stored as a WebGPU buffer.");return this.gpuBufferData}get mlTensor(){if(this.ensureValid(),!this.mlTensorData)throw new Error("The data is not stored as a WebNN MLTensor.");return this.mlTensorData}async getData(t){switch(this.ensureValid(),this.dataLocation){case"cpu":case"cpu-pinned":return this.data;case"texture":case"gpu-buffer":case"ml-tensor":{if(!this.downloader)throw new Error("The current tensor is not created with a specified data downloader.");if(this.isDownloading)throw new Error("The current tensor is being downloaded.");try{this.isDownloading=!0;let i=await this.downloader();return this.downloader=void 0,this.dataLocation="cpu",this.cpuData=i,t&&this.disposer&&(this.disposer(),this.disposer=void 0),i}finally{this.isDownloading=!1}}default:throw new Error(`cannot get data from location: ${this.dataLocation}`)}}dispose(){if(this.isDownloading)throw new Error("The current tensor is being downloaded.");this.disposer&&(this.disposer(),this.disposer=void 0),this.cpuData=void 0,this.gpuTextureData=void 0,this.gpuBufferData=void 0,this.mlTensorData=void 0,this.downloader=void 0,this.isDownloading=void 0,this.dataLocation="none"}ensureValid(){if(this.dataLocation==="none")throw new Error("The tensor is disposed.")}reshape(t){if(this.ensureValid(),this.downloader||this.disposer)throw new Error("Cannot reshape a tensor that owns GPU resource.");return _r(this,t)}}}),St,dr=m(()=>{er(),St=_t}),hr,Ct,He,Rt,sr,tr,Wr=m(()=>{ae(),hr=(t,i)=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||console.timeStamp(`${t}::ORT::${i}`)},Ct=(t,i)=>{var l;let n=((l=new Error().stack)==null?void 0:l.split(/\r\n|\r|\n/g))||[],u=!1;for(let c=0;c<n.length;c++){if(u&&!n[c].includes("TRACE_FUNC")){let h=`FUNC_${t}::${n[c].trim().split(" ")[1]}`;i&&(h+=`::${i}`),hr("CPU",h);return}n[c].includes("TRACE_FUNC")&&(u=!0)}},He=t=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||Ct("BEGIN",t)},Rt=t=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||Ct("END",t)},sr=t=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||console.time(`ORT::${t}`)},tr=t=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||console.timeEnd(`ORT::${t}`)}}),$i,jn=m(()=>{U(),dr(),Wr(),$i=class Nw{constructor(i){this.handler=i}async run(i,n,u){He(),sr("InferenceSession.run");let l={},c={};if(typeof i!="object"||i===null||i instanceof St||Array.isArray(i))throw new TypeError("'feeds' must be an object that use input names as keys and OnnxValue as corresponding values.");let h=!0;if(typeof n=="object"){if(n===null)throw new TypeError("Unexpected argument[1]: cannot be null.");if(n instanceof St)throw new TypeError("'fetches' cannot be a Tensor");if(Array.isArray(n)){if(n.length===0)throw new TypeError("'fetches' cannot be an empty array.");h=!1;for(let $ of n){if(typeof $!="string")throw new TypeError("'fetches' must be a string array or an object.");if(this.outputNames.indexOf($)===-1)throw new RangeError(`'fetches' contains invalid output name: ${$}.`);l[$]=null}if(typeof u=="object"&&u!==null)c=u;else if(typeof u<"u")throw new TypeError("'options' must be an object.")}else{let $=!1,k=Object.getOwnPropertyNames(n);for(let I of this.outputNames)if(k.indexOf(I)!==-1){let O=n[I];(O===null||O instanceof St)&&($=!0,h=!1,l[I]=O)}if($){if(typeof u=="object"&&u!==null)c=u;else if(typeof u<"u")throw new TypeError("'options' must be an object.")}else c=n}}else if(typeof n<"u")throw new TypeError("Unexpected argument[1]: must be 'fetches' or 'options'.");for(let $ of this.inputNames)if(typeof i[$]>"u")throw new Error(`input '${$}' is missing in 'feeds'.`);if(h)for(let $ of this.outputNames)l[$]=null;let y=await this.handler.run(i,l,c),b={};for(let $ in y)if(Object.hasOwnProperty.call(y,$)){let k=y[$];k instanceof St?b[$]=k:b[$]=new St(k.type,k.data,k.dims)}return tr("InferenceSession.run"),Rt(),b}async release(){return this.handler.dispose()}static async create(i,n,u,l){He(),sr("InferenceSession.create");let c,h={};if(typeof i=="string"){if(c=i,typeof n=="object"&&n!==null)h=n;else if(typeof n<"u")throw new TypeError("'options' must be an object.")}else if(i instanceof Uint8Array){if(c=i,typeof n=="object"&&n!==null)h=n;else if(typeof n<"u")throw new TypeError("'options' must be an object.")}else if(i instanceof ArrayBuffer||typeof SharedArrayBuffer<"u"&&i instanceof SharedArrayBuffer){let k=i,I=0,O=i.byteLength;if(typeof n=="object"&&n!==null)h=n;else if(typeof n=="number"){if(I=n,!Number.isSafeInteger(I))throw new RangeError("'byteOffset' must be an integer.");if(I<0||I>=k.byteLength)throw new RangeError(`'byteOffset' is out of range [0, ${k.byteLength}).`);if(O=i.byteLength-I,typeof u=="number"){if(O=u,!Number.isSafeInteger(O))throw new RangeError("'byteLength' must be an integer.");if(O<=0||I+O>k.byteLength)throw new RangeError(`'byteLength' is out of range (0, ${k.byteLength-I}].`);if(typeof l=="object"&&l!==null)h=l;else if(typeof l<"u")throw new TypeError("'options' must be an object.")}else if(typeof u<"u")throw new TypeError("'byteLength' must be a number.")}else if(typeof n<"u")throw new TypeError("'options' must be an object.");c=new Uint8Array(k,I,O)}else throw new TypeError("Unexpected argument[0]: must be 'path' or 'buffer'.");let[y,b]=await H(h),$=await y.createInferenceSessionHandler(c,b);return tr("InferenceSession.create"),Rt(),new Nw($)}startProfiling(){this.handler.startProfiling()}endProfiling(){this.handler.endProfiling()}get inputNames(){return this.handler.inputNames}get outputNames(){return this.handler.outputNames}get inputMetadata(){return this.handler.inputMetadata}get outputMetadata(){return this.handler.outputMetadata}}}),Rr,Kn=m(()=>{jn(),Rr=$i}),Zn=m(()=>{}),Qn=m(()=>{}),Xn=m(()=>{}),oi=m(()=>{}),sn={};_(sn,{InferenceSession:()=>Rr,TRACE:()=>hr,TRACE_EVENT_BEGIN:()=>sr,TRACE_EVENT_END:()=>tr,TRACE_FUNC_BEGIN:()=>He,TRACE_FUNC_END:()=>Rt,Tensor:()=>St,env:()=>B,registerBackend:()=>A});var Kt=m(()=>{P(),me(),Kn(),dr(),Zn(),Qn(),Wr(),Xn(),oi()}),ui=m(()=>{}),on={};_(on,{default:()=>ka});var Gr,Yi,ka,Yn=m(()=>{var t;Sm(),kr(),qi(),Gr="ort-wasm-proxy-worker",Yi=((t=globalThis.self)==null?void 0:t.name)===Gr,Yi&&(self.onmessage=i=>{let{type:n,in:u}=i.data;try{switch(n){case"init-wasm":Wi(u.wasm).then(()=>{Fl(u).then(()=>{postMessage({type:n})},l=>{postMessage({type:n,err:l})})},l=>{postMessage({type:n,err:l})});break;case"init-ep":{let{epName:l,env:c}=u;Hl(c,l).then(()=>{postMessage({type:n})},h=>{postMessage({type:n,err:h})});break}case"copy-from":{let{buffer:l}=u,c=Du(l);postMessage({type:n,out:c});break}case"create":{let{model:l,options:c}=u;Kl(l,c).then(h=>{postMessage({type:n,out:h})},h=>{postMessage({type:n,err:h})});break}case"release":Zl(u),postMessage({type:n});break;case"run":{let{sessionId:l,inputIndices:c,inputs:h,outputIndices:y,options:b}=u;Xl(l,c,h,y,new Array(y.length).fill(null),b).then($=>{$.some(k=>k[3]!=="cpu")?postMessage({type:n,err:"Proxy does not support non-cpu tensor location."}):postMessage({type:n,out:$},Jl([...h,...$]))},$=>{postMessage({type:n,err:$})});break}case"end-profiling":Yl(u),postMessage({type:n});break;default:}}catch(l){postMessage({type:n,err:l})}}),ka=Yi?null:i=>new Worker(i??Ut,{type:"classic",name:Gr})}),Ji,ea,Ut,ta,vi,un,ln,ra,Ea,Ui,dn,Li,Ia,qi=m(()=>{ui(),Ji=typeof location>"u"?void 0:location.origin,ea=()=>{var t,i;return typeof document<"u"?(t=document.currentScript)==null?void 0:t.src:typeof self<"u"?(i=self.location)==null?void 0:i.href:void 0},Ut=ea(),ta=()=>{if(Ut&&!Ut.startsWith("blob:"))return Ut.substring(0,Ut.lastIndexOf("/")+1)},vi=(t,i)=>{try{let n=i??Ut;return(n?new URL(t,n):new URL(t)).origin===Ji}catch{return!1}},un=(t,i)=>{let n=i??Ut;try{return(n?new URL(t,n):new URL(t)).href}catch{return}},ln=(t,i)=>`${i??"./"}${t}`,ra=async t=>{let i=await(await fetch(t,{credentials:"same-origin"})).blob();return URL.createObjectURL(i)},Ea=async t=>(await import(t)).default,Ui=(Yn(),x(on)).default,dn=async()=>{if(!Ut)throw new Error("Failed to load proxy worker: cannot determine the script source URL.");if(vi(Ut))return[void 0,Ui()];let t=await ra(Ut);return[t,Ui(t)]},Li=void 0,Ia=async(t,i,n,u)=>{let l=Li&&!(t||i);if(l)if(Ut)l=vi(Ut)||u&&!n;else if(u&&!n)l=!0;else throw new Error("cannot determine the script source URL.");if(l)return[void 0,Li];{let c="ort-wasm-simd-threaded.jsep.mjs",h=t??un(c,i),y=n&&h&&!vi(h,i),b=y?await ra(h):h??ln(c,i);return[y?b:void 0,await Ea(b)]}}}),Lt,li,Fr,Vi,za,Ca,Aa,Wi,st,kr=m(()=>{qi(),li=!1,Fr=!1,Vi=!1,za=()=>{if(typeof SharedArrayBuffer>"u")return!1;try{return typeof MessageChannel<"u"&&new MessageChannel().port1.postMessage(new SharedArrayBuffer(1)),WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,5,4,1,3,1,1,10,11,1,9,0,65,0,254,16,2,0,26,11]))}catch{return!1}},Ca=()=>{try{return WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,10,30,1,28,0,65,0,253,15,253,12,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,253,186,1,26,11]))}catch{return!1}},Aa=()=>{try{return WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,5,1,96,0,1,123,3,2,1,0,10,19,1,17,0,65,1,253,15,65,2,253,15,65,3,253,15,253,147,2,11]))}catch{return!1}},Wi=async t=>{if(li)return Promise.resolve();if(Fr)throw new Error("multiple calls to 'initializeWebAssembly()' detected.");if(Vi)throw new Error("previous call to 'initializeWebAssembly()' failed.");Fr=!0;let i=t.initTimeout,n=t.numThreads;if(t.simd!==!1){if(t.simd==="relaxed"){if(!Aa())throw new Error("Relaxed WebAssembly SIMD is not supported in the current environment.")}else if(!Ca())throw new Error("WebAssembly SIMD is not supported in the current environment.")}let u=za();n>1&&!u&&(typeof self<"u"&&!self.crossOriginIsolated&&console.warn("env.wasm.numThreads is set to "+n+", but this will not work unless you enable crossOriginIsolated mode. See https://web.dev/cross-origin-isolation-guide/ for more info."),console.warn("WebAssembly multi-threading is not supported in the current environment. Falling back to single-threading."),t.numThreads=n=1);let l=t.wasmPaths,c=typeof l=="string"?l:void 0,h=l==null?void 0:l.mjs,y=(h==null?void 0:h.href)??h,b=l==null?void 0:l.wasm,$=(b==null?void 0:b.href)??b,k=t.wasmBinary,[I,O]=await Ia(y,c,n>1,!!k||!!$),M=!1,D=[];if(i>0&&D.push(new Promise(L=>{setTimeout(()=>{M=!0,L()},i)})),D.push(new Promise((L,Z)=>{let W={numThreads:n};if(k)W.wasmBinary=k,W.locateFile=V=>V;else if($||c)W.locateFile=V=>$??c+V;else if(y&&y.indexOf("blob:")!==0)W.locateFile=V=>new URL(V,y).href;else if(I){let V=ta();V&&(W.locateFile=J=>V+J)}O(W).then(V=>{Fr=!1,li=!0,Lt=V,L(),I&&URL.revokeObjectURL(I)},V=>{Fr=!1,Vi=!0,Z(V)})})),await Promise.race(D),M)throw new Error(`WebAssembly backend initializing failed due to timeout: ${i}ms`)},st=()=>{if(li&&Lt)return Lt;throw new Error("WebAssembly is not initialized yet.")}}),Ht,di,Ye,Gi=m(()=>{kr(),Ht=(t,i)=>{let n=st(),u=n.lengthBytesUTF8(t)+1,l=n._malloc(u);return n.stringToUTF8(t,l,u),i.push(l),l},di=(t,i,n,u)=>{if(typeof t=="object"&&t!==null){if(n.has(t))throw new Error("Circular reference in options");n.add(t)}Object.entries(t).forEach(([l,c])=>{let h=i?i+l:l;if(typeof c=="object")di(c,h+".",n,u);else if(typeof c=="string"||typeof c=="number")u(h,c.toString());else if(typeof c=="boolean")u(h,c?"1":"0");else throw new Error(`Can't handle extra config type: ${typeof c}`)})},Ye=t=>{let i=st(),n=i.stackSave();try{let u=i.PTR_SIZE,l=i.stackAlloc(2*u);i._OrtGetLastError(l,l+u);let c=Number(i.getValue(l,u===4?"i32":"i64")),h=i.getValue(l+u,"*"),y=h?i.UTF8ToString(h):"";throw new Error(`${t} ERROR_CODE: ${c}, ERROR_MESSAGE: ${y}`)}finally{i.stackRestore(n)}}}),pn,Hr=m(()=>{kr(),Gi(),pn=t=>{let i=st(),n=0,u=[],l=t||{};try{if((t==null?void 0:t.logSeverityLevel)===void 0)l.logSeverityLevel=2;else if(typeof t.logSeverityLevel!="number"||!Number.isInteger(t.logSeverityLevel)||t.logSeverityLevel<0||t.logSeverityLevel>4)throw new Error(`log severity level is not valid: ${t.logSeverityLevel}`);if((t==null?void 0:t.logVerbosityLevel)===void 0)l.logVerbosityLevel=0;else if(typeof t.logVerbosityLevel!="number"||!Number.isInteger(t.logVerbosityLevel))throw new Error(`log verbosity level is not valid: ${t.logVerbosityLevel}`);(t==null?void 0:t.terminate)===void 0&&(l.terminate=!1);let c=0;return(t==null?void 0:t.tag)!==void 0&&(c=Ht(t.tag,u)),n=i._OrtCreateRunOptions(l.logSeverityLevel,l.logVerbosityLevel,!!l.terminate,c),n===0&&Ye("Can't create run options."),(t==null?void 0:t.extra)!==void 0&&di(t.extra,"",new WeakSet,(h,y)=>{let b=Ht(h,u),$=Ht(y,u);i._OrtAddRunConfigEntry(n,b,$)!==0&&Ye(`Can't set a run config entry: ${h} - ${y}.`)}),[n,u]}catch(c){throw n!==0&&i._OrtReleaseRunOptions(n),u.forEach(h=>i._free(h)),c}}}),ia,aa,pi,Zt,Oa,cn,_s=m(()=>{kr(),Gi(),ia=t=>{switch(t){case"disabled":return 0;case"basic":return 1;case"extended":return 2;case"layout":return 3;case"all":return 99;default:throw new Error(`unsupported graph optimization level: ${t}`)}},aa=t=>{switch(t){case"sequential":return 0;case"parallel":return 1;default:throw new Error(`unsupported execution mode: ${t}`)}},pi=t=>{t.extra||(t.extra={}),t.extra.session||(t.extra.session={});let i=t.extra.session;i.use_ort_model_bytes_directly||(i.use_ort_model_bytes_directly="1"),t.executionProviders&&t.executionProviders.some(n=>(typeof n=="string"?n:n.name)==="webgpu")&&(t.enableMemPattern=!1)},Zt=(t,i,n,u)=>{let l=Ht(i,u),c=Ht(n,u);st()._OrtAddSessionConfigEntry(t,l,c)!==0&&Ye(`Can't set a session config entry: ${i} - ${n}.`)},Oa=async(t,i,n)=>{let u=i.executionProviders;for(let l of u){let c=typeof l=="string"?l:l.name,h=[];switch(c){case"webnn":if(c="WEBNN",Zt(t,"session.disable_quant_qdq","1",n),Zt(t,"session.disable_qdq_constant_folding","1",n),typeof l!="string"){let I=l==null?void 0:l.deviceType;I&&Zt(t,"deviceType",I,n)}break;case"webgpu":if(c="JS",typeof l!="string"){let I=l;if(I!=null&&I.preferredLayout){if(I.preferredLayout!=="NCHW"&&I.preferredLayout!=="NHWC")throw new Error(`preferredLayout must be either 'NCHW' or 'NHWC': ${I.preferredLayout}`);Zt(t,"preferredLayout",I.preferredLayout,n)}}break;case"wasm":case"cpu":continue;default:throw new Error(`not supported execution provider: ${c}`)}let y=Ht(c,n),b=h.length,$=0,k=0;if(b>0){$=st()._malloc(b*st().PTR_SIZE),n.push($),k=st()._malloc(b*st().PTR_SIZE),n.push(k);for(let I=0;I<b;I++)st().setValue($+I*st().PTR_SIZE,h[I][0],"*"),st().setValue(k+I*st().PTR_SIZE,h[I][1],"*")}await st()._OrtAppendExecutionProvider(t,y,$,k,b)!==0&&Ye(`Can't append execution provider: ${c}.`)}},cn=async t=>{let i=st(),n=0,u=[],l=t||{};pi(l);try{let c=ia(l.graphOptimizationLevel??"all"),h=aa(l.executionMode??"sequential"),y=typeof l.logId=="string"?Ht(l.logId,u):0,b=l.logSeverityLevel??2;if(!Number.isInteger(b)||b<0||b>4)throw new Error(`log severity level is not valid: ${b}`);let $=l.logVerbosityLevel??0;if(!Number.isInteger($)||$<0||$>4)throw new Error(`log verbosity level is not valid: ${$}`);let k=typeof l.optimizedModelFilePath=="string"?Ht(l.optimizedModelFilePath,u):0;if(n=i._OrtCreateSessionOptions(c,!!l.enableCpuMemArena,!!l.enableMemPattern,h,!!l.enableProfiling,0,y,b,$,k),n===0&&Ye("Can't create session options."),l.executionProviders&&await Oa(n,l,u),l.enableGraphCapture!==void 0){if(typeof l.enableGraphCapture!="boolean")throw new Error(`enableGraphCapture must be a boolean value: ${l.enableGraphCapture}`);Zt(n,"enableGraphCapture",l.enableGraphCapture.toString(),u)}if(l.freeDimensionOverrides)for(let[I,O]of Object.entries(l.freeDimensionOverrides)){if(typeof I!="string")throw new Error(`free dimension override name must be a string: ${I}`);if(typeof O!="number"||!Number.isInteger(O)||O<0)throw new Error(`free dimension override value must be a non-negative integer: ${O}`);let M=Ht(I,u);i._OrtAddFreeDimensionOverride(n,M,O)!==0&&Ye(`Can't set a free dimension override: ${I} - ${O}.`)}return l.extra!==void 0&&di(l.extra,"",new WeakSet,(I,O)=>{Zt(n,I,O,u)}),[n,u]}catch(c){throw n!==0&&i._OrtReleaseSessionOptions(n)!==0&&Ye("Can't release session options."),u.forEach(h=>i._free(h)),c}}}),Er,rr,wr,pr,fr,xi,na,Ra,it=m(()=>{Er=t=>{switch(t){case"int8":return 3;case"uint8":return 2;case"bool":return 9;case"int16":return 5;case"uint16":return 4;case"int32":return 6;case"uint32":return 12;case"float16":return 10;case"float32":return 1;case"float64":return 11;case"string":return 8;case"int64":return 7;case"uint64":return 13;case"int4":return 22;case"uint4":return 21;default:throw new Error(`unsupported data type: ${t}`)}},rr=t=>{switch(t){case 3:return"int8";case 2:return"uint8";case 9:return"bool";case 5:return"int16";case 4:return"uint16";case 6:return"int32";case 12:return"uint32";case 10:return"float16";case 1:return"float32";case 11:return"float64";case 8:return"string";case 7:return"int64";case 13:return"uint64";case 22:return"int4";case 21:return"uint4";default:throw new Error(`unsupported data type: ${t}`)}},wr=(t,i)=>{let n=[-1,4,1,1,2,2,4,8,-1,1,2,8,4,8,-1,-1,-1,-1,-1,-1,-1,.5,.5][t],u=typeof i=="number"?i:i.reduce((l,c)=>l*c,1);return n>0?Math.ceil(u*n):void 0},pr=t=>{switch(t){case"float16":return typeof Float16Array<"u"&&Float16Array.from?Float16Array:Uint16Array;case"float32":return Float32Array;case"uint8":return Uint8Array;case"int8":return Int8Array;case"uint16":return Uint16Array;case"int16":return Int16Array;case"int32":return Int32Array;case"bool":return Uint8Array;case"float64":return Float64Array;case"uint32":return Uint32Array;case"int64":return BigInt64Array;case"uint64":return BigUint64Array;default:throw new Error(`unsupported type: ${t}`)}},fr=t=>{switch(t){case"verbose":return 0;case"info":return 1;case"warning":return 2;case"error":return 3;case"fatal":return 4;default:throw new Error(`unsupported logging level: ${t}`)}},xi=t=>t==="float32"||t==="float16"||t==="int32"||t==="int64"||t==="uint32"||t==="uint8"||t==="bool"||t==="uint4"||t==="int4",na=t=>t==="float32"||t==="float16"||t==="int32"||t==="int64"||t==="uint32"||t==="uint64"||t==="int8"||t==="uint8"||t==="bool"||t==="uint4"||t==="int4",Ra=t=>{switch(t){case"none":return 0;case"cpu":return 1;case"cpu-pinned":return 2;case"texture":return 3;case"gpu-buffer":return 4;case"ml-tensor":return 5;default:throw new Error(`unsupported data location: ${t}`)}}}),sa,hn=m(()=>{ui(),sa=async t=>{if(typeof t=="string"){let i=await fetch(t);if(!i.ok)throw new Error(`failed to load external data file: ${t}`);let n=i.headers.get("Content-Length"),u=n?parseInt(n,10):0;if(u<1073741824)return new Uint8Array(await i.arrayBuffer());{if(!i.body)throw new Error(`failed to load external data file: ${t}, no response body.`);let l=i.body.getReader(),c;try{c=new ArrayBuffer(u)}catch(y){if(y instanceof RangeError){let b=Math.ceil(u/65536);c=new WebAssembly.Memory({initial:b,maximum:b}).buffer}else throw y}let h=0;for(;;){let{done:y,value:b}=await l.read();if(y)break;let $=b.byteLength;new Uint8Array(c,h,$).set(b),h+=$}return new Uint8Array(c,0,u)}}else return t instanceof Blob?new Uint8Array(await t.arrayBuffer()):t instanceof Uint8Array?t:new Uint8Array(t)}}),fn,Br,Si,ci,oa,Ba,mt,Mr=m(()=>{it(),fn=["V","I","W","E","F"],Br=(t,i)=>{console.log(`[${fn[t]},${new Date().toISOString()}]${i}`)},oa=(t,i)=>{Si=t,ci=i},Ba=(t,i)=>{let n=fr(t),u=fr(Si);n>=u&&Br(n,typeof i=="function"?i():i)},mt=(...t)=>{ci&&Ba(...t)}}),Ma,hi,he,Fi,Da,mn,zr,Xe=m(()=>{Ma=class{static calcMatMulShape(t,i){return t[1]!==i[0]?void 0:[t[0],i[1]]}},hi=class{static calcShape(t,i,n=!1){let u=t.length,l=i.length;if(u===0)return i;if(l===0)return t;let c=Math.max(t.length,i.length),h=new Array(c);if(n){if(u<2||l<2)return;let y=Ma.calcMatMulShape([t[u-2],t[u-1]],[i[l-2],i[l-1]]);if(y===void 0)return;[h[c-2],h[c-1]]=y}for(let y=n?3:1;y<=c;y++){let b=u-y<0?1:t[u-y],$=l-y<0?1:i[l-y];if(b!==$&&b>1&&$>1)return;let k=Math.max(b,$);if(b&&$)h[c-y]=Math.max(b,$);else{if(k>1)return;h[c-y]=0}}return h}static isValidBroadcast(t,i){let n=t.length,u=i.length;if(n>u)return!1;for(let l=1;l<=n;l++)if(t[n-l]!==1&&t[n-l]!==i[u-l])return!1;return!0}},he=class Yu{static size(i){return Yu.getSizeFromDimensionRange(i,0,i.length)}static convertShape(i,n=4){let u=i.length;if(u===0)return[];let l=new Array(u),c=u-1;for(;c>=0;){if(i[c]%n===0){l[c]=i[c]/n;break}if(n%i[c]!==0)throw new Error("cannot convert shape");l[c]=1,n/=i[c],c--}for(c--;c>=0;c--)l[c]=i[c];return l}static sizeFromDimension(i,n){if(n<0||n>i.length)throw new Error(`invalid dimension of ${n} for sizeFromDimension as Tensor has ${i.length} dimensions.`);return Yu.getSizeFromDimensionRange(i,n,i.length)}static sizeToDimension(i,n){if(n<0||n>i.length)throw new Error(`invalid dimension of ${n} for sizeToDimension as Tensor has ${i.length} dimensions.`);return Yu.getSizeFromDimensionRange(i,0,n)}static getSizeFromDimensionRange(i,n,u){let l=1;for(let c=n;c<u;c++){if(i[c]<0)throw new Error("cannot get valid size from specified dimension range. Most likely the range contains negative values in them.");l*=Number(i[c])}return l}static computeStrides(i){let n=i.length;if(n===0)return[];if(n===1)return[1];let u=new Array(n);u[n-1]=1,u[n-2]=i[n-1];for(let l=n-3;l>=0;--l)u[l]=u[l+1]*i[l+1];return u}static normalizeAxis(i,n){if(i<-n&&i>=n)throw new Error("unsupported axis for this operation.");return i<0?i+n:i}static normalizeAxes(i,n){return i.map(u=>this.normalizeAxis(u,n??i.length))}static sortBasedOnPerm(i,n){return n?n.map(u=>i[u]):i.slice().reverse()}static padShape(i,n){let u=i.length;return i.map((l,c)=>l+n[c]+n[c+u])}static areEqual(i,n){return i.length!==n.length?!1:i.every((u,l)=>u===n[l])}},Fi=class Po{static adjustPoolAttributes(i,n,u,l,c,h){if(!i&&u.length!==n.length-2)throw new Error("length of specified kernel shapes should be 2 less than length of input dimensions");if(i)for(let y=0;y<n.length-2;y++)y>=u.length?u.push(n[y+2]):u[y]=n[y+2];for(let y=0;y<u.length;y++)if(y<l.length){if(l[y]<0)throw new Error("strides should be greater than or equal to 1")}else l.push(1);for(let y=0;y<u.length;y++)if(y<c.length){if(c[y]<0)throw new Error("dilations should be greater than or equal to 1")}else c.push(1);for(let y=0;y<u.length*2;y++)if(y<h.length){if(h[y]<0)throw new Error("pad should be greater than or equal to 1")}else h.push(0);for(let y=0;y<u.length;y++){if(u[y]<=0)throw new Error("kernel shapes need to be greater than 0");if(h[y]>=u[y]||h[y+u.length]>=u[y])throw new Error("pads should be smaller than kernel")}}static adjustPadsBasedOnAutoPad(i,n,u,l,c,h,y){if(y){if(c.length!==2*(i.length-2))throw new Error("length of pads should be twice the length of data dimensions");if(n.length!==i.length-2)throw new Error("length of strides should be the length of data dimensions");if(l.length!==i.length-2)throw new Error("length of kernel shapes should be the length of data dimensions");for(let b=0;b<i.length-2;b++)Po.adjustPadAndReturnShape(i[b+(h?1:2)],n[b],u[b],l[b],c,b,b+i.length-2,y)}}static computePoolOutputShape(i,n,u,l,c,h,y){if(n.length<=0)throw new Error("input shape must be of size greater than 0");let b=[n[0],n[1]];return Po.computeShapeHelper(i,n,b,u,l,c,h,y),b}static computeConvOutputShape(i,n,u,l,c,h,y){if(i.length<=0||n.length<=0)throw new Error("invalid input tensor dims or invalid filter tensor dims");let b=[i[0],n[0]];return Po.computeShapeHelper(!1,i,b,u,l,c,h,y),b}static computeShapeHelper(i,n,u,l,c,h,y,b){if(i)for(let $=0;$<n.length-2;$++)u.push(1);else for(let $=0;$<n.length-2;$++)u.push(Po.adjustPadAndReturnShape(n[$+2],l[$],c[$],h[$],y,$,$+n.length-2,b))}static adjustPadAndReturnShape(i,n,u,l,c,h,y,b){let $=u*(l-1)+1;if(b&&b!=="NOTSET")switch(b){case"VALID":return c[h]=0,c[y]=0,Math.floor((i-$)/n+1);case"SAME_LOWER":case"SAME_UPPER":if(u!==1)throw new Error("Dilation not supported for SAME_UPPER or SAME_LOWER");{let k=((i+n-1)/n-1)*n+l-i;return c[h]=Math.floor(b==="SAME_LOWER"?(k+1)/2:k/2),c[y]=k-c[h],Math.floor((i+k-l)/n+1)}default:throw new Error("Unsupported AutoPad type")}else return Math.floor((i+c[h]+c[y]-$)/n+1)}},Da=class{static getShapeOfGemmResult(t,i,n,u,l){if(t.length!==2||n.length!==2)throw new Error("shape need to be of size 2");let c,h,y;i?(c=t[1],h=t[0]):(c=t[0],h=t[1]);let b=-1;if(u?(y=n[0],b=1):(y=n[1],b=0),n[b]!==h)throw new Error("dimension mismatch");if(c<=0||y<=0||h<=0)throw new Error("invalid shape specified");if(l&&!hi.isValidBroadcast(l,[c,y]))throw new Error("gemm: invalid bias shape for broadcast");return[c,y,h]}},mn=-34028234663852886e22,zr=34028234663852886e22}),jr,Jr=m(()=>{it(),jr=(t,i)=>new(pr(i))(t)}),Ti,fi,ua,la,ei,mi,ki,Na,Dr,Cr,Pa,da=m(()=>{it(),Mr(),Ti=new Map([["float32",32],["float16",16],["int32",32],["uint32",32],["int64",64],["uint64",64],["int8",8],["uint8",8],["int4",4],["uint4",4]]),fi=(t,i)=>{if(i==="int32")return t;let n=Ti.get(i);if(!n)throw new Error(`WebNN backend does not support data type: ${i}`);let u=n/8;if(t.byteLength%u!==0)throw new Error(`Invalid Uint8Array length - must be a multiple of ${u}.`);let l=t.byteLength/u,c=new(pr(i))(t.buffer,t.byteOffset,l);switch(i){case"int64":case"uint64":{let h=new Int32Array(l);for(let y=0;y<l;y++){let b=c[y];if(b>2147483647n||b<-2147483648n)throw new Error("Can not convert int64 data to int32 - value out of range.");h[y]=Number(b)}return new Uint8Array(h.buffer)}case"int8":case"uint8":case"uint32":{if(i==="uint32"&&c.some(y=>y>2147483647))throw new Error("Can not convert uint32 data to int32 - value out of range.");let h=Int32Array.from(c,Number);return new Uint8Array(h.buffer)}default:throw new Error(`Unsupported data conversion from ${i} to 'int32'`)}},ua=(t,i)=>{if(i==="int32")return t;if(t.byteLength%4!==0)throw new Error("Invalid Uint8Array length - must be a multiple of 4 (int32).");let n=t.byteLength/4,u=new Int32Array(t.buffer,t.byteOffset,n);switch(i){case"int64":{let l=BigInt64Array.from(u,BigInt);return new Uint8Array(l.buffer)}case"uint64":{if(u.some(c=>c<0))throw new Error("Can not convert int32 data to uin64 - negative value found.");let l=BigUint64Array.from(u,BigInt);return new Uint8Array(l.buffer)}case"int8":{if(u.some(c=>c<-128||c>127))throw new Error("Can not convert int32 data to int8 - value out of range.");let l=Int8Array.from(u,Number);return new Uint8Array(l.buffer)}case"uint8":{if(u.some(l=>l<0||l>255))throw new Error("Can not convert int32 data to uint8 - value out of range.");return Uint8Array.from(u,Number)}case"uint32":{if(u.some(c=>c<0))throw new Error("Can not convert int32 data to uint32 - negative value found.");let l=Uint32Array.from(u,Number);return new Uint8Array(l.buffer)}default:throw new Error(`Unsupported data conversion from 'int32' to ${i}`)}},la=1,ei=()=>la++,mi=new Map([["int8","int32"],["uint8","int32"],["uint32","int32"],["int64","int32"]]),ki=(t,i)=>{let n=Ti.get(t);if(!n)throw new Error(`WebNN backend does not support data type: ${t}`);return i.length>0?Math.ceil(i.reduce((u,l)=>u*l)*n/8):0},Na=class{constructor(t){this.isDataConverted=!1;let{sessionId:i,context:n,tensor:u,dataType:l,shape:c,fallbackDataType:h}=t;this.sessionId=i,this.mlContext=n,this.mlTensor=u,this.dataType=l,this.tensorShape=c,this.fallbackDataType=h}get tensor(){return this.mlTensor}get type(){return this.dataType}get fallbackType(){return this.fallbackDataType}get shape(){return this.tensorShape}get byteLength(){return ki(this.dataType,this.tensorShape)}destroy(){mt("verbose",()=>"[WebNN] TensorWrapper.destroy"),this.mlTensor.destroy()}write(t){this.mlContext.writeTensor(this.mlTensor,t)}async read(t){if(this.fallbackDataType){let i=await this.mlContext.readTensor(this.mlTensor),n=ua(new Uint8Array(i),this.dataType);if(t){(t instanceof ArrayBuffer?new Uint8Array(t):new Uint8Array(t.buffer,t.byteOffset,t.byteLength)).set(n);return}else return n.buffer}else return t?this.mlContext.readTensor(this.mlTensor,t):this.mlContext.readTensor(this.mlTensor)}canReuseTensor(t,i,n){return this.mlContext===t&&this.dataType===i&&this.tensorShape.length===n.length&&this.tensorShape.every((u,l)=>u===n[l])}setIsDataConverted(t){this.isDataConverted=t}},Dr=class{constructor(t,i){this.tensorManager=t,this.wrapper=i}get tensorWrapper(){return this.wrapper}releaseTensor(){this.tensorWrapper&&(this.tensorManager.releaseTensor(this.tensorWrapper),this.wrapper=void 0)}async ensureTensor(t,i,n,u){let l=this.tensorManager.getMLContext(t),c=this.tensorManager.getMLOpSupportLimits(t),h;if(!(c!=null&&c.input.dataTypes.includes(i))){if(h=mi.get(i),!h||(c==null?void 0:c.input.dataTypes.includes(h)))throw new Error(`WebNN backend does not support data type: ${i}`);mt("verbose",()=>`[WebNN] TensorIdTracker.ensureTensor: fallback dataType from ${i} to ${h}`)}if(this.wrapper){if(this.wrapper.canReuseTensor(l,i,n))return this.wrapper.tensor;if(u){if(this.wrapper.byteLength!==ki(i,n))throw new Error("Unable to copy data to tensor with different size.");this.activeUpload=new Uint8Array(await this.wrapper.read())}this.tensorManager.releaseTensor(this.wrapper)}let y=typeof MLTensorUsage>"u"?void 0:MLTensorUsage.READ|MLTensorUsage.WRITE;return this.wrapper=await this.tensorManager.getCachedTensor(t,i,n,y,!0,!0,h),u&&this.activeUpload&&(this.wrapper.write(this.activeUpload),this.activeUpload=void 0),this.wrapper.tensor}upload(t){let i=t;if(this.wrapper){if(this.wrapper.fallbackType)if(this.wrapper.fallbackType==="int32")i=fi(t,this.wrapper.type),this.wrapper.setIsDataConverted(!0);else throw new Error(`Unsupported fallback data type: ${this.wrapper.fallbackType}`);if(t.byteLength===this.wrapper.byteLength){this.wrapper.write(i);return}else mt("verbose",()=>"Data size does not match tensor size. Releasing tensor."),this.releaseTensor()}this.activeUpload?this.activeUpload.set(i):this.activeUpload=new Uint8Array(i)}async download(t){var i,n;if(this.activeUpload){let u=(i=this.wrapper)!=null&&i.isDataConverted?ua(this.activeUpload,(n=this.wrapper)==null?void 0:n.type):this.activeUpload;if(t){t instanceof ArrayBuffer?new Uint8Array(t).set(u):new Uint8Array(t.buffer,t.byteOffset,t.byteLength).set(u);return}else return u.buffer}if(!this.wrapper)throw new Error("Tensor has not been created.");return t?this.wrapper.read(t):this.wrapper.read()}},Cr=class{constructor(t){this.backend=t,this.tensorTrackersById=new Map,this.freeTensors=[],this.externalTensors=new Set}getMLContext(t){let i=this.backend.getMLContext(t);if(!i)throw new Error("MLContext not found for session.");return i}getMLOpSupportLimits(t){return this.backend.getMLOpSupportLimits(t)}reserveTensorId(){let t=ei();return this.tensorTrackersById.set(t,new Dr(this)),t}releaseTensorId(t){let i=this.tensorTrackersById.get(t);i&&(this.tensorTrackersById.delete(t),i.tensorWrapper&&this.releaseTensor(i.tensorWrapper))}async ensureTensor(t,i,n,u,l){mt("verbose",()=>`[WebNN] TensorManager.ensureTensor {tensorId: ${i}, dataType: ${n}, shape: ${u}, copyOld: ${l}}`);let c=this.tensorTrackersById.get(i);if(!c)throw new Error("Tensor not found.");return c.ensureTensor(t,n,u,l)}upload(t,i){let n=this.tensorTrackersById.get(t);if(!n)throw new Error("Tensor not found.");n.upload(i)}async download(t,i){mt("verbose",()=>`[WebNN] TensorManager.download {tensorId: ${t}, dstBuffer: ${i==null?void 0:i.byteLength}}`);let n=this.tensorTrackersById.get(t);if(!n)throw new Error("Tensor not found.");return n.download(i)}releaseTensorsForSession(t){for(let i of this.freeTensors)i.sessionId===t&&i.destroy();this.freeTensors=this.freeTensors.filter(i=>i.sessionId!==t)}registerTensor(t,i,n,u){let l=this.getMLContext(t),c=ei(),h=new Na({sessionId:t,context:l,tensor:i,dataType:n,shape:u});return this.tensorTrackersById.set(c,new Dr(this,h)),this.externalTensors.add(h),c}async getCachedTensor(t,i,n,u,l,c,h){let y=this.getMLContext(t);for(let[$,k]of this.freeTensors.entries())if(k.canReuseTensor(y,i,n)){mt("verbose",()=>`[WebNN] Reusing tensor {dataType: ${i}, ${h?`fallbackDataType: ${h},`:""} shape: ${n}`);let I=this.freeTensors.splice($,1)[0];return I.sessionId=t,I}mt("verbose",()=>`[WebNN] MLContext.createTensor {dataType: ${i}, ${h?`fallbackDataType: ${h},`:""} shape: ${n}}`);let b=await y.createTensor({dataType:h??i,shape:n,dimensions:n,usage:u,writable:l,readable:c});return new Na({sessionId:t,context:y,tensor:b,dataType:i,shape:n,fallbackDataType:h})}releaseTensor(t){this.externalTensors.has(t)&&this.externalTensors.delete(t),this.freeTensors.push(t)}},Pa=(...t)=>new Cr(...t)}),gi,Ua,La,gn=m(()=>{it(),kr(),Jr(),da(),Mr(),gi=new Map([[1,"float32"],[10,"float16"],[6,"int32"],[12,"uint32"],[7,"int64"],[13,"uint64"],[22,"int4"],[21,"uint4"],[3,"int8"],[2,"uint8"],[9,"uint8"]]),Ua=(t,i)=>{if(t===i)return!0;if(t===void 0||i===void 0)return!1;let n=Object.keys(t).sort(),u=Object.keys(i).sort();return n.length===u.length&&n.every((l,c)=>l===u[c]&&t[l]===i[l])},La=class{constructor(t){this.tensorManager=Pa(this),this.mlContextBySessionId=new Map,this.sessionIdsByMLContext=new Map,this.mlContextCache=[],this.sessionGraphInputs=new Map,this.sessionGraphOutputs=new Map,this.temporaryGraphInputs=[],this.temporaryGraphOutputs=[],this.temporarySessionTensorIds=new Map,this.mlOpSupportLimitsBySessionId=new Map,oa(t.logLevel,!!t.debug)}get currentSessionId(){if(this.activeSessionId===void 0)throw new Error("No active session");return this.activeSessionId}onRunStart(t){mt("verbose",()=>`[WebNN] onRunStart {sessionId: ${t}}`),this.activeSessionId=t}onRunEnd(t){mt("verbose",()=>`[WebNN] onRunEnd {sessionId: ${t}}`);let i=this.temporarySessionTensorIds.get(t);if(i){for(let n of i)mt("verbose",()=>`[WebNN] releasing temporary tensor {tensorId: ${n}}`),this.tensorManager.releaseTensorId(n);this.temporarySessionTensorIds.delete(t),this.activeSessionId=void 0}}async createMLContext(t){if(t instanceof GPUDevice){let n=this.mlContextCache.findIndex(u=>u.gpuDevice===t);if(n!==-1)return this.mlContextCache[n].mlContext;{let u=await navigator.ml.createContext(t);return this.mlContextCache.push({gpuDevice:t,mlContext:u}),u}}else if(t===void 0){let n=this.mlContextCache.findIndex(u=>u.options===void 0&&u.gpuDevice===void 0);if(n!==-1)return this.mlContextCache[n].mlContext;{let u=await navigator.ml.createContext();return this.mlContextCache.push({mlContext:u}),u}}let i=this.mlContextCache.findIndex(n=>Ua(n.options,t));if(i!==-1)return this.mlContextCache[i].mlContext;{let n=await navigator.ml.createContext(t);return this.mlContextCache.push({options:t,mlContext:n}),n}}registerMLContext(t,i){this.mlContextBySessionId.set(t,i);let n=this.sessionIdsByMLContext.get(i);n||(n=new Set,this.sessionIdsByMLContext.set(i,n)),n.add(t),this.mlOpSupportLimitsBySessionId.has(t)||this.mlOpSupportLimitsBySessionId.set(t,i.opSupportLimits()),this.temporaryGraphInputs.length>0&&(this.sessionGraphInputs.set(t,this.temporaryGraphInputs),this.temporaryGraphInputs=[]),this.temporaryGraphOutputs.length>0&&(this.sessionGraphOutputs.set(t,this.temporaryGraphOutputs),this.temporaryGraphOutputs=[])}onReleaseSession(t){this.sessionGraphInputs.delete(t),this.sessionGraphOutputs.delete(t);let i=this.mlContextBySessionId.get(t);if(!i)return;this.tensorManager.releaseTensorsForSession(t),this.mlContextBySessionId.delete(t),this.mlOpSupportLimitsBySessionId.delete(t);let n=this.sessionIdsByMLContext.get(i);if(n.delete(t),n.size===0){this.sessionIdsByMLContext.delete(i);let u=this.mlContextCache.findIndex(l=>l.mlContext===i);u!==-1&&this.mlContextCache.splice(u,1)}}getMLContext(t){return this.mlContextBySessionId.get(t)}getMLOpSupportLimits(t){return this.mlOpSupportLimitsBySessionId.get(t)}reserveTensorId(){return this.tensorManager.reserveTensorId()}releaseTensorId(t){mt("verbose",()=>`[WebNN] releaseTensorId {tensorId: ${t}}`),this.tensorManager.releaseTensorId(t)}async ensureTensor(t,i,n,u,l){let c=gi.get(n);if(!c)throw new Error(`Unsupported ONNX data type: ${n}`);return this.tensorManager.ensureTensor(t??this.currentSessionId,i,c,u,l)}async createTemporaryTensor(t,i,n){mt("verbose",()=>`[WebNN] createTemporaryTensor {onnxDataType: ${i}, shape: ${n}}`);let u=gi.get(i);if(!u)throw new Error(`Unsupported ONNX data type: ${i}`);let l=this.tensorManager.reserveTensorId();await this.tensorManager.ensureTensor(t,l,u,n,!1);let c=this.temporarySessionTensorIds.get(t);return c?c.push(l):this.temporarySessionTensorIds.set(t,[l]),l}uploadTensor(t,i){if(!st().shouldTransferToMLTensor)throw new Error("Trying to upload to a MLTensor while shouldTransferToMLTensor is false");mt("verbose",()=>`[WebNN] uploadTensor {tensorId: ${t}, data: ${i.byteLength}}`),this.tensorManager.upload(t,i)}async downloadTensor(t,i){return this.tensorManager.download(t,i)}createMLTensorDownloader(t,i){return async()=>{let n=await this.tensorManager.download(t);return jr(n,i)}}registerMLTensor(t,i,n,u){let l=gi.get(n);if(!l)throw new Error(`Unsupported ONNX data type: ${n}`);let c=this.tensorManager.registerTensor(t,i,l,u);return mt("verbose",()=>`[WebNN] registerMLTensor {tensor: ${i}, dataType: ${l}, dimensions: ${u}} -> {tensorId: ${c}}`),c}registerMLConstant(t,i,n,u,l,c,h=!1){if(!c)throw new Error("External mounted files are not available.");let y=t;t.startsWith("./")&&(y=t.substring(2));let b=c.get(y);if(!b)throw new Error(`File with name ${y} not found in preloaded files.`);if(i+n>b.byteLength)throw new Error("Out of bounds: data offset and length exceed the external file data size.");let $=b.slice(i,i+n).buffer,k;switch(l.dataType){case"float32":k=new Float32Array($);break;case"float16":k=typeof Float16Array<"u"&&Float16Array.from?new Float16Array($):new Uint16Array($);break;case"int32":k=new Int32Array($);break;case"uint32":k=new Uint32Array($);break;case"int64":if(h){let I=fi(new Uint8Array($),"int64");k=new Int32Array(I.buffer),l.dataType="int32"}else k=new BigInt64Array($);break;case"uint64":k=new BigUint64Array($);break;case"int8":k=new Int8Array($);break;case"int4":case"uint4":case"uint8":k=new Uint8Array($);break;default:throw new Error(`Unsupported data type: ${l.dataType} in creating WebNN Constant from external data.`)}return mt("verbose",()=>`[WebNN] registerMLConstant {dataType: ${l.dataType}, shape: ${l.shape}}} ${h?"(Note: it was int64 data type and registered to int32 as workaround)":""}`),u.constant(l,k)}registerGraphInput(t){this.temporaryGraphInputs.push(t)}registerGraphOutput(t){this.temporaryGraphOutputs.push(t)}isGraphInput(t,i){let n=this.sessionGraphInputs.get(t);return n?n.includes(i):!1}isGraphOutput(t,i){let n=this.sessionGraphOutputs.get(t);return n?n.includes(i):!1}isGraphInputOutputTypeSupported(t,i,n=!0){let u=gi.get(Er(i)),l=this.mlOpSupportLimitsBySessionId.get(t);return typeof u>"u"?!1:n?!!(l!=null&&l.input.dataTypes.includes(u)):!!(l!=null&&l.output.dataTypes.includes(u))}flush(){}}}),Hi=m(()=>{}),qa,pa,ca,ji,Va,Wa,yn,_n,ha,ws=m(()=>{Mr(),Hi(),qa=new Map([[64,250],[128,200],[256,200],[512,200],[2048,230],[4096,200],[8192,50],[16384,50],[32768,50],[65536,50],[131072,50],[262144,50],[524288,50],[1048576,50],[2097152,30],[4194304,20],[8388608,10],[12582912,10],[16777216,10],[26214400,15],[33554432,22],[44236800,2],[58982400,6],[67108864,6],[134217728,6],[167772160,6]]),pa=[],ca=t=>Math.ceil(Number(t)/16)*16,ji=t=>{for(let i=0;i<pa.length;i++){let n=pa[i];if(t<=n)return n}return Math.ceil(t/16)*16},Va=1,Wa=()=>Va++,yn=async(t,i,n,u)=>{let l=ca(n),c=t.device.createBuffer({size:l,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});try{let h=t.getCommandEncoder();t.endComputePass(),h.copyBufferToBuffer(i,0,c,0,l),t.flush(),await c.mapAsync(GPUMapMode.READ);let y=c.getMappedRange();if(u){let b=u();return b.set(new Uint8Array(y,0,n)),b}else return new Uint8Array(y.slice(0,n))}finally{c.destroy()}},_n=class{constructor(t){this.backend=t,this.storageCache=new Map,this.freeBuffers=new Map,this.freeUniformBuffers=new Map,this.buffersPending=[],this.capturedPendingBuffers=new Map;for(let[i]of qa)pa.push(i),this.freeBuffers.set(i,[]),this.freeUniformBuffers.set(i,[]);this.sessionCount=0}upload(t,i){let n=i.buffer,u=i.byteOffset,l=i.byteLength,c=ca(l),h=this.storageCache.get(t);if(!h)throw new Error("gpu data for uploading does not exist");if(Number(h.originalSize)!==l)throw new Error(`inconsistent data size. gpu data size=${h.originalSize}, data size=${l}`);let y=this.backend.device.createBuffer({mappedAtCreation:!0,size:c,usage:GPUBufferUsage.MAP_WRITE|GPUBufferUsage.COPY_SRC}),b=y.getMappedRange();new Uint8Array(b).set(new Uint8Array(n,u,l)),y.unmap();let $=this.backend.device.createCommandEncoder();$.copyBufferToBuffer(y,0,h.gpuData.buffer,0,c),this.backend.device.queue.submit([$.finish()]),y.destroy(),mt("verbose",()=>`[WebGPU] GpuDataManager.upload(id=${t})`)}memcpy(t,i){let n=this.storageCache.get(t);if(!n)throw new Error("source gpu data for memcpy does not exist");let u=this.storageCache.get(i);if(!u)throw new Error("destination gpu data for memcpy does not exist");if(n.originalSize!==u.originalSize)throw new Error("inconsistent source and destination gpu data size");let l=ca(n.originalSize),c=this.backend.getCommandEncoder();this.backend.endComputePass(),c.copyBufferToBuffer(n.gpuData.buffer,0,u.gpuData.buffer,0,l)}registerExternalBuffer(t,i,n){let u;if(n){if(u=n[0],t===n[1])return mt("verbose",()=>`[WebGPU] GpuDataManager.registerExternalBuffer(size=${i}) => id=${u}, buffer is the same, skip.`),u;if(this.backend.capturedCommandList.has(this.backend.currentSessionId))throw new Error(`Registering a different external buffer under graph capture mode is not supported yet.
             Please use the previous external buffer!`)}else u=Wa();return this.storageCache.set(u,{gpuData:{id:u,type:0,buffer:t},originalSize:i}),mt("verbose",()=>`[WebGPU] GpuDataManager.registerExternalBuffer(size=${i}) => id=${u}, registered.`),u}unregisterExternalBuffer(t){t!==void 0&&(this.storageCache.delete(t),mt("verbose",()=>`[WebGPU] GpuDataManager.unregisterExternalBuffer() => id=${t}`))}create(t,i=GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC|GPUBufferUsage.COPY_DST){let n=ji(t),u,l=(i&GPUBufferUsage.STORAGE)===GPUBufferUsage.STORAGE,c=(i&GPUBufferUsage.UNIFORM)===GPUBufferUsage.UNIFORM;if(l||c){let y=(l?this.freeBuffers:this.freeUniformBuffers).get(n);y?y.length>0?u=y.pop():u=this.backend.device.createBuffer({size:n,usage:i}):u=this.backend.device.createBuffer({size:n,usage:i})}else u=this.backend.device.createBuffer({size:n,usage:i});let h={id:Wa(),type:0,buffer:u};return this.storageCache.set(h.id,{gpuData:h,originalSize:Number(t)}),mt("verbose",()=>`[WebGPU] GpuDataManager.create(size=${t}) => id=${h.id}`),h}get(t){var i;return(i=this.storageCache.get(t))==null?void 0:i.gpuData}release(t){let i=typeof t=="bigint"?Number(t):t,n=this.storageCache.get(i);if(!n){if(this.storageCache.size===0)return 0;throw new Error("releasing data does not exist")}return mt("verbose",()=>`[WebGPU] GpuDataManager.release(id=${i}), gpuDataId=${n.gpuData.id}`),this.storageCache.delete(i),this.buffersPending.push(n.gpuData.buffer),n.originalSize}async download(t,i){let n=this.storageCache.get(Number(t));if(!n)throw new Error("data does not exist");await yn(this.backend,n.gpuData.buffer,n.originalSize,i)}refreshPendingBuffers(){if(this.buffersPending.length!==0)if(this.backend.sessionStatus==="default"){for(let t of this.buffersPending){let i=qa.get(t.size);if((t.usage&GPUBufferUsage.STORAGE)===GPUBufferUsage.STORAGE){let n=this.freeBuffers.get(t.size)||[];i===void 0||n.length>=i?t.destroy():n.push(t)}else if((t.usage&GPUBufferUsage.UNIFORM)===GPUBufferUsage.UNIFORM){let n=this.freeUniformBuffers.get(t.size)||[];i===void 0||n.length>=i?t.destroy():n.push(t)}else t.destroy()}this.buffersPending=[]}else{let t=this.capturedPendingBuffers.get(this.backend.currentSessionId);t||(t=[],this.capturedPendingBuffers.set(this.backend.currentSessionId,t));for(let i of this.buffersPending)t.push(i);this.buffersPending=[]}}dispose(){this.freeBuffers.forEach(t=>{t.forEach(i=>{i.destroy()})}),this.freeUniformBuffers.forEach(t=>{t.forEach(i=>{i.destroy()})}),this.storageCache.forEach(t=>{t.gpuData.buffer.destroy()}),this.capturedPendingBuffers.forEach(t=>{t.forEach(i=>{i.destroy()})}),this.storageCache=new Map,this.freeBuffers=new Map,this.freeUniformBuffers=new Map,this.capturedPendingBuffers=new Map}onCreateSession(){this.sessionCount+=1}onReleaseSession(t){let i=this.capturedPendingBuffers.get(t);i&&(i.forEach(n=>{n.destroy()}),this.capturedPendingBuffers.delete(t)),this.sessionCount-=1,this.sessionCount===0&&(mt("warning",()=>"[WebGPU] Clearing webgpu buffer cache"),this.storageCache.forEach(n=>{n.gpuData.buffer.destroy()}),this.storageCache=new Map)}},ha=(...t)=>new _n(...t)}),z,N,j=m(()=>{z=class{constructor(t){Object.assign(this,t)}get cacheKey(){return this.key||(this.key=Object.getOwnPropertyNames(this).sort().map(t=>`${this[t]}`).join(";")),this.key}},N=t=>new z(t)}),te,X,ue,re,ie,le,ve,Se,be,ce,Fe,oe,ke,qt,dt,lt,Mt,Ke=m(()=>{it(),Xe(),te=64,X=(t,i)=>{if(i===3)throw new Error("vec3 has same alignment as vec4, use vec4 instead");switch(Number(t)){case 10:return i>1?`vec${i}<f16>`:"f16";case 1:return i>1?`vec${i}<f32>`:"f32";case 6:return i>1?`vec${i}<i32>`:"i32";case 12:return i>1?`vec${i}<u32>`:"u32";case 7:if(i>1)throw new Error("currently not supported vecX of uint64 yet");return["vec2<u32>","i32"];case 13:if(i>1)throw new Error("currently not supported vecX of uint64 yet");return["vec2<u32>","u32"];case 9:if(i!==4)throw new Error("bool must be vec4");return["u32","vec4<bool>"];case 22:return"i32";case 21:return"u32";default:throw new Error(`Unknown data type: ${t}`)}},ue=(t,i=1)=>{let n=X(t,i);return typeof n=="string"?n:n[0]},re=(t,i=1)=>{let n=X(t,i);return typeof n=="string"?n:n[1]},ie=(...t)=>{let i=[];return t.forEach(n=>{n.length!==0&&i.push({type:12,data:n},{type:12,data:he.computeStrides(n)})}),i},le=t=>t%4===0?4:t%2===0?2:1,ve=(t="f32",i,n="0")=>!i||i===1?`${t}(${n})`:`vec${i}<${t}>(${n})`,Se=(t,i,n)=>t==="f32"?n:i===1?`f32(${n})`:`vec${i}<f32>(${n})`,be=(t,i)=>i===4?`(${t}.x + ${t}.y + ${t}.z + ${t}.w)`:i===2?`(${t}.x + ${t}.y)`:i===3?`(${t}.x + ${t}.y + ${t}.z)`:t,ce=(t,i,n,u)=>t.startsWith("uniforms.")&&n>4?typeof i=="string"?u==="f16"?`${t}[(${i}) / 8][(${i}) % 8 / 4][(${i}) % 8 % 4]`:`${t}[(${i}) / 4][(${i}) % 4]`:u==="f16"?`${t}[${Math.floor(i/8)}][${Math.floor(i%8/4)}][${i%8%4}]`:`${t}[${Math.floor(i/4)}][${i%4}]`:n>1?`${t}[${i}]`:t,Fe=(t,i,n,u,l)=>{let c=typeof n=="number",h=c?n:n.length,y=[...new Array(h).keys()],b=h<2?"u32":h<=4?`vec${h}<u32>`:`array<u32, ${h}>`,$=X(i,l),k=typeof $=="string"?$:$[1],I=typeof $=="string"?$:$[0],O={indices:b,value:k,storage:I,tensor:i},M=Ae=>typeof Ae=="string"?Ae:`${Ae}u`,D={offsetToIndices:!1,indicesToOffset:!1,broadcastedIndicesToOffset:!1,set:!1,setByIndices:!1,get:!1,getByIndices:!1},L=c?"uniforms.":"",Z=`${L}${t}_shape`,W=`${L}${t}_strides`,V="";for(let Ae=0;Ae<h-1;Ae++)V+=`
    let dim${Ae} = current / ${ce(W,Ae,h)};
    let rest${Ae} = current % ${ce(W,Ae,h)};
    indices[${Ae}] = dim${Ae};
    current = rest${Ae};
    `;V+=`indices[${h-1}] = current;`;let J=h<2?"":`
  fn o2i_${t}(offset: u32) -> ${O.indices} {
    var indices: ${O.indices};
    var current = offset;
    ${V}
    return indices;
  }`,Y=Ae=>(D.offsetToIndices=!0,h<2?Ae:`o2i_${t}(${Ae})`),se=[];if(h>=2)for(let Ae=h-1;Ae>=0;Ae--)se.push(`${ce(W,Ae,h)} * (indices[${Ae}])`);let de=h<2?"":`
  fn i2o_${t}(indices: ${O.indices}) -> u32 {
    return ${se.join("+")};
  }`,fe=Ae=>(D.indicesToOffset=!0,h<2?Ae:`i2o_${t}(${Ae})`),we=(...Ae)=>h===0?"0u":`${O.indices}(${Ae.map(M).join(",")})`,xe=(Ae,Ge)=>h<2?`${Ae}`:`${ce(Ae,Ge,h)}`,De=(Ae,Ge,Ze)=>h<2?`${Ae}=${Ze};`:`${ce(Ae,Ge,h)}=${Ze};`,at={},et=(Ae,Ge)=>{D.broadcastedIndicesToOffset=!0;let Ze=`${Ge.name}broadcastedIndicesTo${t}Offset`;if(Ze in at)return`${Ze}(${Ae})`;let Pe=[];for(let Vt=h-1;Vt>=0;Vt--){let _a=Ge.indicesGet("outputIndices",Vt+Ge.rank-h);Pe.push(`${xe(W,Vt)} * (${_a} % ${xe(Z,Vt)})`)}return at[Ze]=`fn ${Ze}(outputIndices: ${Ge.type.indices}) -> u32 {
             return ${Pe.length>0?Pe.join("+"):"0u"};
           }`,`${Ze}(${Ae})`},tt=(Ae,Ge)=>(()=>{if(O.storage===O.value)return`${t}[${Ae}]=${Ge};`;if(O.storage==="vec2<u32>"&&O.value==="i32")return`${t}[${Ae}]=vec2<u32>(u32(${Ge}), select(0u, 0xFFFFFFFFu, ${Ge} < 0));`;if(O.storage==="vec2<u32>"&&O.value==="u32")return`${t}[${Ae}]=vec2<u32>(u32(${Ge}), 0u);`;if(O.storage==="u32"&&O.value==="vec4<bool>")return`${t}[${Ae}]=dot(vec4<u32>(0x1, 0x100, 0x10000, 0x1000000), vec4<u32>(${Ge}));`;throw new Error(`not supported combination of storage type ${O.storage} and value type ${O.value} yet`)})(),xt=Ae=>(()=>{if(O.storage===O.value)return`${t}[${Ae}]`;if(O.storage==="vec2<u32>"&&O.value==="i32")return`i32(${t}[${Ae}].x)`;if(O.storage==="vec2<u32>"&&O.value==="u32")return`u32(${t}[${Ae}].x)`;if(O.storage==="u32"&&O.value==="vec4<bool>")return`vec4<bool>(bool(${t}[${Ae}] & 0xFFu), bool(${t}[${Ae}] & 0xFF00u), bool(${t}[${Ae}] & 0xFF0000u), bool(${t}[${Ae}] & 0xFF000000u))`;throw new Error(`not supported combination of storage type ${O.storage} and value type ${O.value} yet`)})(),zt=h<2?"":`
  fn get_${t}ByIndices(indices: ${O.indices}) -> ${k} {
    return ${xt(`i2o_${t}(indices)`)};
  }`,rt=h<2?"":(()=>{let Ae=y.map(Ze=>`d${Ze}: u32`).join(", "),Ge=y.map(Ze=>`d${Ze}`).join(", ");return`
  fn get_${t}(${Ae}) -> ${k} {
    return get_${t}ByIndices(${we(Ge)});
  }`})(),ot=(...Ae)=>{if(Ae.length!==h)throw new Error(`indices length must be ${h}`);let Ge=Ae.map(M).join(",");return h===0?xt("0u"):h===1?xt(Ge[0]):(D.get=!0,D.getByIndices=!0,D.indicesToOffset=!0,`get_${t}(${Ge})`)},ur=Ae=>h<2?xt(Ae):(D.getByIndices=!0,D.indicesToOffset=!0,`get_${t}ByIndices(${Ae})`),Ne=h<2?"":`
  fn set_${t}ByIndices(indices: ${O.indices}, value: ${k}) {
    ${tt(`i2o_${t}(indices)`,"value")}
  }`,Ot=h<2?"":(()=>{let Ae=y.map(Ze=>`d${Ze}: u32`).join(", "),Ge=y.map(Ze=>`d${Ze}`).join(", ");return`
  fn set_${t}(${Ae}, value: ${k}) {
    set_${t}ByIndices(${we(Ge)}, value);
  }`})();return{impl:()=>{let Ae=[],Ge=!1;return D.offsetToIndices&&(Ae.push(J),Ge=!0),D.indicesToOffset&&(Ae.push(de),Ge=!0),D.broadcastedIndicesToOffset&&(Object.values(at).forEach(Ze=>Ae.push(Ze)),Ge=!0),D.set&&(Ae.push(Ot),Ge=!0),D.setByIndices&&(Ae.push(Ne),Ge=!0),D.get&&(Ae.push(rt),Ge=!0),D.getByIndices&&(Ae.push(zt),Ge=!0),!c&&Ge&&Ae.unshift(`const ${Z} = ${O.indices}(${n.join(",")});`,`const ${W} = ${O.indices}(${he.computeStrides(n).join(",")});`),Ae.join(`
`)},type:O,offsetToIndices:Y,indicesToOffset:fe,broadcastedIndicesToOffset:et,indices:we,indicesGet:xe,indicesSet:De,set:(...Ae)=>{if(Ae.length!==h+1)throw new Error(`indices length must be ${h}`);let Ge=Ae[h];if(typeof Ge!="string")throw new Error("value must be string");let Ze=Ae.slice(0,h).map(M).join(",");return h===0?tt("0u",Ge):h===1?tt(Ze[0],Ge):(D.set=!0,D.setByIndices=!0,D.indicesToOffset=!0,`set_${t}(${Ze}, ${Ge})`)},setByOffset:tt,setByIndices:(Ae,Ge)=>h<2?tt(Ae,Ge):(D.setByIndices=!0,D.indicesToOffset=!0,`set_${t}ByIndices(${Ae}, ${Ge});`),get:ot,getByOffset:xt,getByIndices:ur,usage:u,name:t,strides:W,shape:Z,rank:h}},oe=(t,i,n,u=1)=>Fe(t,i,n,"input",u),ke=(t,i,n,u=1)=>Fe(t,i,n,"output",u),qt=(t,i,n)=>Fe(t,i,n,"atomicOutput",1),dt=(t,i,n,u=1)=>Fe(t,i,n,"internal",u),lt=class{constructor(t,i){this.normalizedDispatchGroup=t,this.limits=i,this.internalVariables=[],this.variables=[],this.uniforms=[],this.variableIndex=0}guardAgainstOutOfBoundsWorkgroupSizes(t){return`if (global_idx >= ${typeof t=="number"?`${t}u`:t}) { return; }`}mainStart(t=te){let i=typeof t=="number"?t:t[0],n=typeof t=="number"?1:t[1],u=typeof t=="number"?1:t[2];if(i>this.limits.maxComputeWorkgroupSizeX||n>this.limits.maxComputeWorkgroupSizeY||u>this.limits.maxComputeWorkgroupSizeZ)throw new Error(`workgroup size [${i}, ${n}, ${u}] exceeds the maximum workgroup size [${this.limits.maxComputeWorkgroupSizeX}, ${this.limits.maxComputeWorkgroupSizeY}, ${this.limits.maxComputeWorkgroupSizeZ}].`);if(i*n*u>this.limits.maxComputeInvocationsPerWorkgroup)throw new Error(`workgroup size [${i}, ${n}, ${u}] exceeds the maximum workgroup invocations ${this.limits.maxComputeInvocationsPerWorkgroup}.`);let l=this.normalizedDispatchGroup[1]===1&&this.normalizedDispatchGroup[2]===1,c=l?`@builtin(global_invocation_id) global_id : vec3<u32>,
    @builtin(workgroup_id) workgroup_id : vec3<u32>,
    @builtin(local_invocation_index) local_idx : u32,
    @builtin(local_invocation_id) local_id : vec3<u32>`:`@builtin(global_invocation_id) global_id : vec3<u32>,
                                             @builtin(local_invocation_id) local_id : vec3<u32>,
    @builtin(local_invocation_index) local_idx : u32,
    @builtin(workgroup_id) workgroup_id : vec3<u32>,
    @builtin(num_workgroups) num_workgroups : vec3<u32>`,h=l?`let global_idx = global_id.x;
         let workgroup_index = workgroup_id.x;`:`let workgroup_index = workgroup_id.z * num_workgroups[0] * num_workgroups[1] +
             workgroup_id.y * num_workgroups[0] + workgroup_id.x;
         let global_idx = workgroup_index * ${i*n*u}u + local_idx;`;return`@compute @workgroup_size(${i}, ${n}, ${u})
  fn main(${c}) {
    ${h}
  `}appendVariableUniforms(t){t.rank!==0&&(t.shape.startsWith("uniforms.")&&this.uniforms.push({name:t.shape.replace("uniforms.",""),type:"u32",length:t.rank}),t.strides.startsWith("uniforms.")&&this.uniforms.push({name:t.strides.replace("uniforms.",""),type:"u32",length:t.rank}))}declareVariable(t,i){if(t.usage==="internal")throw new Error("cannot use internal variable with declareVariable(). use registerInternalVariables() instead.");this.variables.push(t),this.appendVariableUniforms(t);let n=t.usage==="input"?"read":"read_write",u=t.usage==="atomicOutput"?"atomic<i32>":t.type.storage;return`@group(0) @binding(${i}) var<storage, ${n}> ${t.name}: array<${u}>;`}declareVariables(...t){return t.map(i=>this.declareVariable(i,this.variableIndex++)).join(`
`)}registerInternalVariable(t){if(t.usage!=="internal")throw new Error("cannot use input or output variable with registerInternalVariable(). use declareVariables() instead.");this.internalVariables.push(t),this.appendVariableUniforms(t)}registerInternalVariables(...t){return t.forEach(i=>this.registerInternalVariable(i)),this}registerUniform(t,i,n=1){return this.uniforms.push({name:t,type:i,length:n}),this}registerUniforms(t){return this.uniforms=this.uniforms.concat(t),this}uniformDeclaration(){if(this.uniforms.length===0)return"";let t=[];for(let{name:i,type:n,length:u}of this.uniforms)if(u&&u>4)n==="f16"?t.push(`@align(16) ${i}:array<mat2x4<${n}>, ${Math.ceil(u/8)}>`):t.push(`${i}:array<vec4<${n}>, ${Math.ceil(u/4)}>`);else{let l=u==null||u===1?n:`vec${u}<${n}>`;t.push(`${i}:${l}`)}return`
      struct Uniforms { ${t.join(", ")} };
      @group(0) @binding(${this.variableIndex}) var<uniform> uniforms: Uniforms;`}get additionalImplementations(){return this.uniformDeclaration()+this.variables.map(t=>t.impl()).join(`
`)+this.internalVariables.map(t=>t.impl()).join(`
`)}get variablesInfo(){if(this.uniforms.length===0)return;let t=i=>[12,10,1,6][["u32","f16","f32","i32"].indexOf(i)];return this.uniforms.map(i=>[t(i.type),i.length??1])}},Mt=(t,i)=>new lt(t,i)}),Dt,vt,ir,mr,br,fa,cr,Ga,wn,or=m(()=>{it(),Xe(),j(),Ke(),Dt=(t,i)=>{if(!t||t.length!==1)throw new Error("Transpose requires 1 input.");if(i.length!==0&&i.length!==t[0].dims.length)throw new Error(`perm size ${i.length} does not match input rank ${t[0].dims.length}`)},vt=(t,i)=>i.length!==0?i:[...new Array(t).keys()].reverse(),ir=(t,i)=>he.sortBasedOnPerm(t,vt(t.length,i)),mr=(t,i,n,u)=>{let l=`fn perm(i: ${u.type.indices}) -> ${n.type.indices} {
    var a: ${n.type.indices};`;for(let c=0;c<i;++c)l+=`a[${t[c]}]=i[${c}];`;return l+="return a;}"},br=(t,i)=>{let n=[],u=[];for(let l=0;l<t.length;++l)t[l]!==1&&n.push(t[l]),t[i[l]]!==1&&u.push(i[l]);return{newShape:n,newPerm:u}},fa=(t,i)=>{let n=0;for(let u=0;u<t.length;++u)if(i[t[u]]!==1){if(t[u]<n)return!1;n=t[u]}return!0},cr=(t,i)=>{let n=t.dataType,u=t.dims.length,l=vt(u,i),c=ir(t.dims,l),h=t.dims,y=c,b=u<2||fa(l,t.dims),$;if(b)return $=D=>{let L=oe("input",n,h,4),Z=ke("output",n,y,4);return`
  ${D.registerUniform("output_size","u32").declareVariables(L,Z)}
  ${D.mainStart()}
    ${D.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    output[global_idx] = input[global_idx];
  }`},{name:"TransposeCopy",shaderCache:{inputDependencies:["type"]},getRunData:()=>{let D=he.size(c);return{outputs:[{dims:c,dataType:t.dataType}],dispatchGroup:{x:Math.ceil(D/64/4)},programUniforms:[{type:12,data:Math.ceil(D/4)}]}},getShaderSource:$};let{newShape:k,newPerm:I}=br(t.dims,l),O=he.areEqual(I,[2,3,1]),M=he.areEqual(I,[3,1,2]);if(k.length===2||O||M){h=O?[k[0],k[1]*k[2]]:M?[k[0]*k[1],k[2]]:k,y=[h[1],h[0]];let D=16;return $=L=>{let Z=oe("a",n,h.length),W=ke("output",n,y.length);return`
  ${L.registerUniform("output_size","u32").declareVariables(Z,W)}
  var<workgroup> tile : array<array<${W.type.value}, ${D+1}>, ${D}>;
  ${L.mainStart([D,D,1])}
    let stride = (uniforms.output_shape[1] - 1) / ${D} + 1;
    let workgroup_id_x = workgroup_index % stride;
    let workgroup_id_y = workgroup_index / stride;
    let input_col = workgroup_id_y * ${D}u + local_id.x;
    let input_row = workgroup_id_x * ${D}u + local_id.y;
    if (input_row < uniforms.a_shape[0] && input_col < uniforms.a_shape[1]) {
      tile[local_id.y][local_id.x] = ${Z.getByIndices(`${Z.type.indices}(input_row, input_col)`)};
    }
    workgroupBarrier();

    let output_col = workgroup_id_x * ${D}u + local_id.x;
    let output_row = workgroup_id_y * ${D}u + local_id.y;
    if (output_row < uniforms.output_shape[0] && output_col < uniforms.output_shape[1]) {
      ${W.setByIndices(`${W.type.indices}(output_row, output_col)`,"tile[local_id.x][local_id.y]")}
    }
  }`},{name:"TransposeShared",shaderCache:{inputDependencies:["type"]},getRunData:()=>{let L=he.size(c);return{outputs:[{dims:c,dataType:t.dataType}],dispatchGroup:{x:Math.ceil(y[1]/D),y:Math.ceil(y[0]/D)},programUniforms:[{type:12,data:L},...ie(h,y)]}},getShaderSource:$}}return $=D=>{let L=oe("a",n,h.length),Z=ke("output",n,y.length);return`
  ${D.registerUniform("output_size","u32").declareVariables(L,Z)}

  ${mr(l,u,L,Z)}

  ${D.mainStart()}
    ${D.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let indices = ${Z.offsetToIndices("global_idx")};
    let aIndices = perm(indices);

    ${Z.setByOffset("global_idx",L.getByIndices("aIndices"))}
  }`},{name:"Transpose",shaderCache:{hint:`${i}`,inputDependencies:["rank"]},getRunData:()=>{let D=he.size(c);return{outputs:[{dims:c,dataType:t.dataType}],dispatchGroup:{x:Math.ceil(D/64)},programUniforms:[{type:12,data:D},...ie(h,y)]}},getShaderSource:$}},Ga=(t,i)=>{Dt(t.inputs,i.perm),t.compute(cr(t.inputs[0],i.perm))},wn=t=>N({perm:t.perm})}),Kr,bn,wt,Nr,Jn,gr,yi,Qt,$r,Ei,vr,$n,Fa,Pr,Zr,Ii,Xt,Gt,Ur,vn,es,jo=m(()=>{it(),Xe(),Ke(),ss(),or(),Kr={max:"select(bestValue, candidate, candidate > bestValue)",min:"select(bestValue, candidate, candidate < bestValue)",mean:"bestValue + candidate",sum:"bestValue + candidate",prod:"bestValue * candidate",sumSquare:"bestValue + candidate * candidate",logSumExp:"bestValue + exp(candidate)",l1:"bestValue + abs(candidate)",l2:"bestValue + candidate * candidate",logSum:"bestValue + candidate"},bn={max:"select(bestValue, candidate, candidate > bestValue)",min:"select(bestValue, candidate, candidate < bestValue)",mean:"bestValue + candidate",sum:"bestValue + candidate",prod:"bestValue * candidate",sumSquare:"bestValue + candidate",logSumExp:"bestValue + candidate",l1:"bestValue + candidate",l2:"bestValue + candidate",logSum:"bestValue + candidate"},wt={max:"_A[offset]",min:"_A[offset]",mean:"0",sum:"0",prod:"1",sumSquare:"0",logSumExp:"0",l1:"0",l2:"0",logSum:"0"},Nr={max:"bestValue",min:"bestValue",sum:"bestValue",prod:"bestValue",sumSquare:"bestValue",logSumExp:"log(bestValue)",l1:"bestValue",l2:"sqrt(bestValue)",logSum:"log(bestValue)"},Jn=(t,i)=>{let n=[];for(let u=i-t;u<i;++u)n.push(u);return n},gr=(t,i)=>{let n=[],u=t.length;for(let c=0;c<u;c++)i.indexOf(c)===-1&&n.push(t[c]);let l=i.map(c=>t[c]);return[n,l]},yi=(t,i)=>{let n=t.length+i.length,u=[],l=0;for(let c=0;c<n;c++)i.indexOf(c)===-1?u.push(t[l++]):u.push(1);return u},Qt=(t,i)=>{for(let n=0;n<t.length;++n)if(t[t.length-n-1]!==i-1-n)return!1;return!0},$r=(t,i)=>{let n=[];if(!Qt(t,i)){for(let u=0;u<i;++u)t.indexOf(u)===-1&&n.push(u);t.forEach(u=>n.push(u))}return n},Ei=(t,i,n,u,l,c,h)=>{let y=n[0].dims,b=he.size(c),$=he.size(h),k=oe("_A",n[0].dataType,y),I=ke("output",l,c),O=64;b===1&&(O=256);let M=`
          var<workgroup> aBestValues : array<f32, ${O}>;
       `,D=L=>`
        ${L.registerUniform("reduceSize","u32").declareVariables(k,I)}
        ${M}
        fn DIV_CEIL(a : u32, b : u32) -> u32 {
          return ((a - 1u) / b + 1u);
         }
         ${L.mainStart(O)}

          let outputIndex = global_idx / ${O};
          let offset = outputIndex * uniforms.reduceSize;

          var bestValue = f32(${wt[u]});
          let Length = uniforms.reduceSize;
          for (var k = local_idx; k < Length; k = k + ${O}) {
           let candidate = f32(${k.getByOffset("offset + k")});
           bestValue = ${Kr[u]};
          }
          aBestValues[local_idx] = bestValue;
          workgroupBarrier();

         var reduceSize = min(Length, ${O}u);
         for (var currentSize = reduceSize / 2u; reduceSize > 1u;
             currentSize = reduceSize / 2u) {
           let interval = DIV_CEIL(reduceSize, 2u);
           if (local_idx < currentSize) {
            let candidate = aBestValues[local_idx + interval];
            bestValue = ${bn[u]};
            aBestValues[local_idx] = bestValue;
           }
           reduceSize = interval;
           workgroupBarrier();
         }

         if (local_idx == 0u) {
          ${I.setByOffset("outputIndex",`${u==="mean"?`${I.type.storage}(bestValue / f32(uniforms.reduceSize))`:`${I.type.storage}(${Nr[u]})`}`)};
         }
        }`;return{name:t,shaderCache:{hint:`${i};${O}`,inputDependencies:["type"]},getShaderSource:D,getRunData:()=>({outputs:[{dims:c,dataType:l}],dispatchGroup:{x:b},programUniforms:[{type:12,data:$}]})}},vr=(t,i,n,u)=>{let l=t.inputs.length===1?n:Ha(t.inputs,n),c=l.axes;c.length===0&&!l.noopWithEmptyAxes&&(c=t.inputs[0].dims.map((M,D)=>D));let h=he.normalizeAxes(c,t.inputs[0].dims.length),y=h,b=t.inputs[0],$=$r(y,t.inputs[0].dims.length);$.length>0&&(b=t.compute(cr(t.inputs[0],$),{inputs:[0],outputs:[-1]})[0],y=Jn(y.length,b.dims.length));let[k,I]=gr(b.dims,y),O=k;l.keepDims&&(O=yi(k,h)),t.compute(Ei(i,l.cacheKey,[b],u,t.inputs[0].dataType,O,I),{inputs:[b]})},$n=(t,i)=>{vr(t,"ReduceMeanShared",i,"mean")},Fa=(t,i)=>{vr(t,"ReduceL1Shared",i,"l1")},Pr=(t,i)=>{vr(t,"ReduceL2Shared",i,"l2")},Zr=(t,i)=>{vr(t,"ReduceLogSumExpShared",i,"logSumExp")},Ii=(t,i)=>{vr(t,"ReduceMaxShared",i,"max")},Xt=(t,i)=>{vr(t,"ReduceMinShared",i,"min")},Gt=(t,i)=>{vr(t,"ReduceProdShared",i,"prod")},Ur=(t,i)=>{vr(t,"ReduceSumShared",i,"sum")},vn=(t,i)=>{vr(t,"ReduceSumSquareShared",i,"sumSquare")},es=(t,i)=>{vr(t,"ReduceLogSumShared",i,"logSum")}}),ti,bs,xn,Ha,ar,ja,ts,$s,vs,xs,rs,Ss,Ts,bt,Ka,Qr,gt,is,yt,ks,as,Es,Is,zs,ns,Cs,ss=m(()=>{it(),Xe(),j(),Ke(),jo(),ti=t=>{if(!t||t.length===0||t.length>2)throw new Error("Reduce op requires 1 or 2 inputs.");if(t.length===2&&t[1].dims.length!==1)throw new Error("Invalid axes input dims.")},bs=t=>["","",`var value = ${t.getByIndices("input_indices")};`,""],xn=(t,i,n,u,l,c,h=!1,y=!1)=>{let b=[],$=n[0].dims,k=$.length,I=he.normalizeAxes(l,k),O=!y&&I.length===0;$.forEach((L,Z)=>{O||I.indexOf(Z)>=0?h&&b.push(1):b.push(L)});let M=b.length,D=he.size(b);return{name:t,shaderCache:i,getShaderSource:L=>{let Z=[],W=oe("_A",n[0].dataType,k),V=ke("output",c,M),J=u(W,V,I),Y=J[2];for(let se=0,de=0;se<k;se++)O||I.indexOf(se)>=0?(h&&de++,Y=`for(var j${se}: u32 = 0; j${se} < ${$[se]}; j${se}++) {
                  ${J[2].includes("last_index")?`let last_index = j${se};`:""}
                  ${W.indicesSet("input_indices",se,`j${se}`)}
                  ${Y}
                }`):(Z.push(`${W.indicesSet("input_indices",se,V.indicesGet("output_indices",de))};`),de++);return`

        ${L.registerUniform("output_size","u32").declareVariables(W,V)}

        ${L.mainStart()}
          ${L.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
          var input_indices: ${W.type.indices};
          let output_indices = ${V.offsetToIndices("global_idx")};

          ${Z.join(`
`)}
          ${J[0]}       // init ops for reduce max/min
          ${J[1]}
          ${Y}
          ${J[3]}
          ${J.length===4?V.setByOffset("global_idx","value"):J.slice(4).join(`
`)}
        }`},getRunData:()=>({outputs:[{dims:b,dataType:c}],dispatchGroup:{x:Math.ceil(D/64)},programUniforms:[{type:12,data:D},...ie($,b)]})}},Ha=(t,i)=>{let n=[];return t[1].dims[0]>0&&t[1].getBigInt64Array().forEach(u=>n.push(Number(u))),N({axes:n,keepDims:i.keepDims,noopWithEmptyAxes:i.noopWithEmptyAxes})},ar=(t,i,n,u)=>{let l=t.inputs,c=l.length===1?n:Ha(l,n);t.compute(xn(i,{hint:c.cacheKey,inputDependencies:["rank"]},[l[0]],c.noopWithEmptyAxes&&c.axes.length===0?bs:u,c.axes,l[0].dataType,c.keepDims,c.noopWithEmptyAxes),{inputs:[0]})},ja=(t,i)=>{ti(t.inputs),ar(t,"ReduceLogSum",i,(n,u)=>[`var value = ${u.type.storage}(0);`,"",`value += ${n.getByIndices("input_indices")};`,"value = log(value);"])},ts=(t,i)=>{ti(t.inputs),ar(t,"ReduceL1",i,(n,u)=>[`var value = ${u.type.storage}(0);`,"",`value += abs(${n.getByIndices("input_indices")});`,""])},$s=(t,i)=>{ti(t.inputs),ar(t,"ReduceL2",i,(n,u)=>[`var t = ${u.type.value}(0); var value = ${u.type.value}(0);`,"",`t = ${n.getByIndices("input_indices")}; value += (t * t);`,"value = sqrt(value);"])},vs=(t,i)=>{ti(t.inputs),ar(t,"ReduceLogSumExp",i,(n,u)=>[`var value = ${u.type.storage}(0);`,"",`value += exp(${n.getByIndices("input_indices")});`,"value = log(value);"])},xs=(t,i)=>{ti(t.inputs),ar(t,"ReduceMax",i,(n,u,l)=>{let c=[];for(let h=0;h<n.rank;h++)(l.indexOf(h)>=0||l.length===0)&&c.push(n.indicesSet("input_indices",h,0));return[`${c.join(`
`)}`,`var value = ${n.getByIndices("input_indices")};`,`value = max(value, ${n.getByIndices("input_indices")});`,""]})},rs=(t,i)=>{ti(t.inputs),ar(t,"ReduceMean",i,(n,u,l)=>{let c=1;for(let h=0;h<n.rank;h++)(l.indexOf(h)>=0||l.length===0)&&(c*=t.inputs[0].dims[h]);return["var sum = f32(0);","",`sum += f32(${n.getByIndices("input_indices")});`,`let value = ${u.type.value}(sum / ${c});`]})},Ss=(t,i)=>{ti(t.inputs),ar(t,"ReduceMin",i,(n,u,l)=>{let c=[];for(let h=0;h<n.rank;h++)(l.indexOf(h)>=0||l.length===0)&&c.push(`input_indices[${h}] = 0;`);return[`${c.join(`
`)}`,`var value = ${n.getByIndices("input_indices")};`,`value = min(value, ${n.getByIndices("input_indices")});`,""]})},Ts=(t,i)=>{ti(t.inputs),ar(t,"ReduceProd",i,(n,u)=>[`var value = ${u.type.storage}(1);`,"",`value *= ${n.getByIndices("input_indices")};`,""])},bt=(t,i)=>{ti(t.inputs),ar(t,"ReduceSum",i,(n,u)=>[`var value = ${u.type.storage}(0);`,"",`value += ${n.getByIndices("input_indices")};`,""])},Ka=(t,i)=>{ti(t.inputs),ar(t,"ReduceSumSquare",i,(n,u)=>[`var t = ${u.type.value}(0); var value = ${u.type.value}(0);`,"",`t = ${n.getByIndices("input_indices")}; value += t * t;`,""])},Qr=(t,i,n)=>{if(i.length===0)return n;let u=1,l=1;for(let c=0;c<i.length;c++)i.indexOf(c)===-1?u*=t[c]:l*=t[c];return l<32&&u>1024},gt=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?rs(t,i):$n(t,i)},is=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?ts(t,i):Fa(t,i)},yt=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?$s(t,i):Pr(t,i)},ks=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?vs(t,i):Zr(t,i)},as=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?xs(t,i):Ii(t,i)},Es=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?Ss(t,i):Xt(t,i)},Is=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?Ts(t,i):Gt(t,i)},zs=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?bt(t,i):Ur(t,i)},ns=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?Ka(t,i):vn(t,i)},Cs=(t,i)=>{Qr(t.inputs[0].dims,i.axes,i.noopWithEmptyAxes)?ja(t,i):es(t,i)}}),os,As,Os,us,Ko=m(()=>{it(),j(),ss(),os=t=>{if(!t||t.length===0||t.length>2)throw new Error("ArgMinMaxOp op requires 1 or 2 inputs.");if(t[0].dataType!==1)throw new Error("Invalid input type.")},As=(t,i)=>{os(t.inputs);let n=(u,l,c)=>{let h=[];for(let y=0;y<u.rank;y++)(c.indexOf(y)>=0||c.length===0)&&h.push(`input_indices[${y}] = 0;`);return[`${h.join(`
`)}`,`var value = ${u.getByIndices("input_indices")};
var best_index : i32 = 0;`,`if (${u.getByIndices("input_indices")} ${i.selectLastIndex>0?"<=":"<"} value) {
         value = ${u.getByIndices("input_indices")};
         best_index = i32(last_index);
       }`,"",l.setByOffset("global_idx","best_index")]};t.compute(xn("ArgMin",{hint:i.cacheKey,inputDependencies:["rank"]},[t.inputs[0]],n,[i.axis],7,i.keepDims),{inputs:[0]})},Os=(t,i)=>{os(t.inputs);let n=(u,l,c)=>{let h=[];for(let y=0;y<u.rank;y++)(c.indexOf(y)>=0||c.length===0)&&h.push(`input_indices[${y}] = 0;`);return[`${h.join(`
`)}`,`var value = ${u.getByIndices("input_indices")};
var best_index : i32 = 0;`,`if (${u.getByIndices("input_indices")} ${i.selectLastIndex>0?">=":">"} value) {
         value = ${u.getByIndices("input_indices")};
         best_index = i32(last_index);
       }`,"",l.setByOffset("global_idx","best_index")]};t.compute(xn("argMax",{hint:i.cacheKey,inputDependencies:["rank"]},[t.inputs[0]],n,[i.axis],7,i.keepDims),{inputs:[0]})},us=t=>N(t)}),Rs,Sn,Bs,Ms,Ds,Za,Ns,Ps,ls=m(()=>{it(),Xe(),Hi(),Ke(),Rs=(t,i)=>{let n=t[0],u=t[1],l=t[2],c=t[3],h=t[4],y=t[5];if(h&&y)throw new Error("Attention cannot have both past and attention_bias");if(n.dims.length!==3)throw new Error('Input "input" must have 3 dimensions');let b=n.dims[0],$=n.dims[1],k=n.dims[2];if(l.dims.length!==1)throw new Error('Input "bias" is expected to have 1 dimensions');if(u.dims.length!==2)throw new Error('Input "weights" is expected to have 2 dimensions');if(u.dims[0]!==k)throw new Error("Input 1 dimension 0 should have same length as dimension 2 of input 0");if(l.dims[0]!==u.dims[1])throw new Error('Input "bias" dimension 0 should have same length as dimension 1 of input "weights"');let I=l.dims[0]/3,O=I,M=O;if(i.qkvHiddenSizes.length>0){if(i.qkvHiddenSizes.length!==3)throw new Error("qkv_hidden_sizes attribute should have 3 elements");for(let J of i.qkvHiddenSizes)if(J%i.numHeads!==0)throw new Error("qkv_hidden_sizes should be divisible by num_heads");I=i.qkvHiddenSizes[0],O=i.qkvHiddenSizes[1],M=i.qkvHiddenSizes[2]}let D=$;if(I!==O)throw new Error("qkv_hidden_sizes first element should be same as the second");if(l.dims[0]!==I+O+M)throw new Error('Input "bias" dimension 0 should have same length as sum of Q/K/V hidden sizes');let L=0;if(h){if(O!==M)throw new Error('Input "past" expect k_hidden_size == v_hidden_size');if(h.dims.length!==5)throw new Error('Input "past" must have 5 dimensions');if(h.dims[0]!==2)throw new Error('Input "past" first dimension must be 2');if(h.dims[1]!==b)throw new Error('Input "past" second dimension must be batch_size');if(h.dims[2]!==i.numHeads)throw new Error('Input "past" third dimension must be num_heads');if(h.dims[4]!==O/i.numHeads)throw new Error('Input "past" fifth dimension must be k_hidden_size / num_heads');i.pastPresentShareBuffer||(L=h.dims[3])}let Z=D+L,W=-1,V=0;if(c)throw new Error("Mask not supported");if(h)throw new Error("past is not supported");if(y){if(y.dims.length!==4)throw new Error('Input "attention_bias" must have 4 dimensions');if(y.dims[0]!==b||y.dims[1]!==i.numHeads||y.dims[2]!==$||y.dims[3]!==Z)throw new Error('Expect "attention_bias" shape (batch_size, num_heads, sequence_length, total_sequence_length)')}return{batchSize:b,sequenceLength:$,pastSequenceLength:L,kvSequenceLength:D,totalSequenceLength:Z,maxSequenceLength:W,inputHiddenSize:k,hiddenSize:I,vHiddenSize:M,headSize:Math.floor(I/i.numHeads),vHeadSize:Math.floor(M/i.numHeads),numHeads:i.numHeads,isUnidirectional:!1,pastPresentShareBuffer:!1,maskFilterValue:i.maskFilterValue,maskType:V,scale:i.scale,broadcastResPosBias:!1,passPastInKv:!1,qkvFormat:1}},Sn=(t,i,n)=>i&&t?`
      let total_sequence_length_input = u32(${i.getByOffset("0")});
      let present_sequence_length = max(total_sequence_length_input, uniforms.past_sequence_length);
      let is_subsequent_prompt: bool = sequence_length > 1 && sequence_length != total_sequence_length_input;
      let is_first_prompt: bool = is_subsequent_prompt == false && sequence_length == total_sequence_length_input;
      total_sequence_length = u32(${t==null?void 0:t.getByOffset("batchIdx")}) + 1;
      var past_sequence_length: u32 = 0;
      if (is_first_prompt == false) {
        past_sequence_length = total_sequence_length - sequence_length;
      }
       `:`
    ${n?"let past_sequence_length = uniforms.past_sequence_length":""};
    let present_sequence_length = total_sequence_length;
    `,Bs=(t,i,n,u,l,c,h,y)=>{let b=le(h?1:c),$=64,k=c/b;k<$&&($=32);let I=Math.ceil(c/b/$),O=[{type:12,data:i},{type:12,data:n},{type:12,data:u},{type:12,data:l},{type:12,data:k},{type:12,data:I}],M=ue(t.dataType,b),D=re(1,b),L=["type"];h&&L.push("type"),y&&L.push("type");let Z=W=>{let V=ke("x",t.dataType,t.dims,b),J=[V],Y=h?oe("seq_lens",h.dataType,h.dims):void 0;Y&&J.push(Y);let se=y?oe("total_sequence_length_input",y.dataType,y.dims):void 0;se&&J.push(se);let de=re(t.dataType),fe=[{name:"batch_size",type:"u32"},{name:"num_heads",type:"u32"},{name:"past_sequence_length",type:"u32"},{name:"sequence_length",type:"u32"},{name:"total_sequence_length",type:"u32"},{name:"elements_per_thread",type:"u32"}];return`
  var<workgroup> thread_max: array<f32, ${$}>;
  var<workgroup> thread_sum: array<f32, ${$}>;
  ${W.registerUniforms(fe).declareVariables(...J)}
  ${W.mainStart([$,1,1])}
    let batchIdx = workgroup_id.z / uniforms.num_heads;
    let headIdx = workgroup_id.z % uniforms.num_heads;
    let sequence_length = uniforms.sequence_length;
    var total_sequence_length = uniforms.total_sequence_length;
    ${Sn(Y,se,!1)}
    let local_offset = local_idx * uniforms.elements_per_thread;
    let offset = (global_idx / ${$}) * uniforms.total_sequence_length + local_offset;
    let seq_causal_length = ${h?"u32(past_sequence_length + workgroup_id.y + 1)":"total_sequence_length"};
    var thread_max_vector = ${D}(-3.4028234663852886e+38f);
    for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
      thread_max_vector = max(${D}(x[offset + i]), thread_max_vector);
    }
    thread_max[local_idx] = ${(()=>{switch(b){case 1:return"thread_max_vector";case 2:return"max(thread_max_vector.x, thread_max_vector.y)";case 4:return"max(max(thread_max_vector.x, thread_max_vector.y), max(thread_max_vector.z, thread_max_vector.w))";default:throw new Error(`Unsupported components: ${b}`)}})()};
    workgroupBarrier();

    var max_value =  f32(-3.4028234663852886e+38f);
    for (var i = 0u; i < ${$}; i++) {
      max_value = max(thread_max[i], max_value);
    }

    var sum_vector = ${D}(0);
    for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
      sum_vector += exp(${D}(x[offset + i]) - max_value);
    }
    thread_sum[local_idx] = ${(()=>{switch(b){case 1:return"sum_vector";case 2:return"sum_vector.x + sum_vector.y";case 4:return"sum_vector.x + sum_vector.y + sum_vector.z + sum_vector.w";default:throw new Error(`Unsupported components: ${b}`)}})()};
    workgroupBarrier();

    var sum: f32 = 0;
    for (var i = 0u; i < ${$}; i++) {
      sum += thread_sum[i];
    }

    if (sum == 0) {
      for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
        x[offset + i] = ${V.type.value}(${de}(1.0) / ${de}(seq_causal_length));
      }
    } else {
      for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
        var f32input = ${D}(x[offset + i]);
        x[offset + i] = ${V.type.value}(exp(f32input - max_value) / sum);
      }
    }
      ${h?`
        for (var total_seq_id: u32 = seq_causal_length; total_seq_id + local_offset < uniforms.total_sequence_length; total_seq_id++) {
          x[offset + total_seq_id] = ${V.type.value}(${de}(0));
        }`:""};
  }`};return{name:"AttentionProbsSoftmax",shaderCache:{hint:`${$};${M};${b}`,inputDependencies:L},getShaderSource:Z,getRunData:()=>({outputs:[],dispatchGroup:{x:1,y:l,z:i*n},programUniforms:O})}},Ms=(t,i,n,u,l,c,h,y,b)=>{let $=h+c.kvSequenceLength,k=[c.batchSize,c.numHeads,c.sequenceLength,$],I=t>1&&u,O=c.kvNumHeads?c.kvNumHeads:c.numHeads,M=I?[c.batchSize,O,$,c.headSize]:void 0,D=c.nReps?c.nReps:1,L=c.scale===0?1/Math.sqrt(c.headSize):c.scale,Z=le(c.headSize),W=c.headSize/Z,V=12,J={x:Math.ceil($/V),y:Math.ceil(c.sequenceLength/V),z:c.batchSize*c.numHeads},Y=[{type:12,data:c.sequenceLength},{type:12,data:W},{type:12,data:$},{type:12,data:c.numHeads},{type:12,data:c.headSize},{type:1,data:L},{type:12,data:h},{type:12,data:c.kvSequenceLength},{type:12,data:D}],se=I&&u&&he.size(u.dims)>0,de=["type","type"];se&&de.push("type"),l&&de.push("type"),y&&de.push("type"),b&&de.push("type");let fe=[{dims:k,dataType:i.dataType,gpuDataType:0}];I&&fe.push({dims:M,dataType:i.dataType,gpuDataType:0});let we=xe=>{let De=oe("q",i.dataType,i.dims,Z),at=oe("key",n.dataType,n.dims,Z),et=[De,at];if(se){let Ne=oe("past_key",u.dataType,u.dims,Z);et.push(Ne)}l&&et.push(oe("attention_bias",l.dataType,l.dims));let tt=y?oe("seq_lens",y.dataType,y.dims):void 0;tt&&et.push(tt);let xt=b?oe("total_sequence_length_input",b.dataType,b.dims):void 0;xt&&et.push(xt);let zt=ke("output",i.dataType,k),rt=[zt];I&&rt.push(ke("present_key",i.dataType,M,Z));let ot=re(1,Z),ur=[{name:"M",type:"u32"},{name:"K",type:"u32"},{name:"N",type:"u32"},{name:"num_heads",type:"u32"},{name:"head_size",type:"u32"},{name:"alpha",type:"f32"},{name:"past_sequence_length",type:"u32"},{name:"kv_sequence_length",type:"u32"},{name:"n_reps",type:"u32"}];return`
  const TILE_SIZE = ${V}u;

  var<workgroup> tileQ: array<${De.type.storage}, ${V*V}>;
  var<workgroup> tileK: array<${De.type.storage}, ${V*V}>;
  ${xe.registerUniforms(ur).declareVariables(...et,...rt)}
  ${xe.mainStart([V,V,1])}
    // x holds the N and y holds the M
    let headIdx = workgroup_id.z % uniforms.num_heads;
    let kvHeadIdx = ${D===1?"headIdx":"headIdx / uniforms.n_reps"};
    let kv_num_heads = ${D===1?"uniforms.num_heads":"uniforms.num_heads / uniforms.n_reps"};
    let batchIdx = workgroup_id.z / uniforms.num_heads;
    let m = workgroup_id.y * TILE_SIZE;
    let n = workgroup_id.x * TILE_SIZE;
    let sequence_length = uniforms.M;
    var total_sequence_length = uniforms.N;
    ${Sn(tt,xt,!0)}
    let absKvHeadIdx = batchIdx * kv_num_heads + kvHeadIdx;
    let qOffset = workgroup_id.z * uniforms.M * uniforms.K + m * uniforms.K;
    ${se&&I?"let pastKeyOffset = absKvHeadIdx * uniforms.past_sequence_length * uniforms.K;":""};
    let kOffset = absKvHeadIdx * uniforms.kv_sequence_length * uniforms.K;
    ${I?"let presentKeyOffset = absKvHeadIdx * uniforms.N * uniforms.K;":""}
    var value = ${ot}(0);
    for (var w: u32 = 0u; w < uniforms.K; w += TILE_SIZE) {
      if (global_id.y < uniforms.M && w + local_id.x < uniforms.K) {
        tileQ[TILE_SIZE * local_id.y + local_id.x] = q[qOffset + local_id.y * uniforms.K + w + local_id.x];
      }
      if (n + local_id.y < uniforms.N && w + local_id.x < uniforms.K) {
        var idx = TILE_SIZE * local_id.y + local_id.x;
      ${se&&I?`
              if (n + local_id.y < past_sequence_length) {
                tileK[idx] = past_key[pastKeyOffset + (n + local_id.y) * uniforms.K + w + local_id.x];
              } else if (n + local_id.y - past_sequence_length < uniforms.kv_sequence_length) {
                tileK[idx] = key[kOffset + (n + local_id.y - past_sequence_length) * uniforms.K + w + local_id.x];
              }`:`
          if (n + local_id.y < uniforms.kv_sequence_length) {
            tileK[idx] = key[kOffset + (n + local_id.y) * uniforms.K + w + local_id.x];
          }`}
      ${I?`if (n + local_id.y < present_sequence_length) {
        present_key[presentKeyOffset + (n + local_id.y) * uniforms.K + w + local_id.x] = tileK[idx];
      }`:""}
      }
      workgroupBarrier();

      for (var k: u32 = 0u; k < TILE_SIZE && w+k < uniforms.K; k++) {
          value += ${ot}(tileQ[TILE_SIZE * local_id.y + k] * tileK[TILE_SIZE * local_id.x + k]);
      }

      workgroupBarrier();
    }

    if (global_id.y < uniforms.M && global_id.x < total_sequence_length) {
      let headOffset = workgroup_id.z * uniforms.M * uniforms.N;
      let outputIdx = headOffset + global_id.y * uniforms.N + global_id.x;
      var sum: f32 = ${(()=>{switch(Z){case 1:return"value";case 2:return"value.x + value.y";case 4:return"value.x + value.y + value.z + value.w";default:throw new Error(`Unsupported components: ${Z}`)}})()};
        output[outputIdx] = ${zt.type.value} (sum * uniforms.alpha) + ${l?"attention_bias[outputIdx]":"0.0"};
    }
  }`};return{name:"AttentionProbs",shaderCache:{hint:`${Z};${l!==void 0};${u!==void 0};${t}`,inputDependencies:de},getRunData:()=>({outputs:fe,dispatchGroup:J,programUniforms:Y}),getShaderSource:we}},Ds=(t,i,n,u,l,c,h=void 0,y=void 0)=>{let b=c+l.kvSequenceLength,$=l.nReps?l.nReps:1,k=l.vHiddenSize*$,I=t>1&&u,O=l.kvNumHeads?l.kvNumHeads:l.numHeads,M=I?[l.batchSize,O,b,l.headSize]:void 0,D=[l.batchSize,l.sequenceLength,k],L=12,Z={x:Math.ceil(l.vHeadSize/L),y:Math.ceil(l.sequenceLength/L),z:l.batchSize*l.numHeads},W=[{type:12,data:l.sequenceLength},{type:12,data:b},{type:12,data:l.vHeadSize},{type:12,data:l.numHeads},{type:12,data:l.headSize},{type:12,data:k},{type:12,data:c},{type:12,data:l.kvSequenceLength},{type:12,data:$}],V=I&&u&&he.size(u.dims)>0,J=["type","type"];V&&J.push("type"),h&&J.push("type"),y&&J.push("type");let Y=[{dims:D,dataType:i.dataType,gpuDataType:0}];I&&Y.push({dims:M,dataType:i.dataType,gpuDataType:0});let se=de=>{let fe=oe("probs",i.dataType,i.dims),we=oe("v",n.dataType,n.dims),xe=[fe,we];V&&xe.push(oe("past_value",u.dataType,u.dims));let De=h?oe("seq_lens",h.dataType,h.dims):void 0;h&&xe.push(De);let at=y?oe("total_sequence_length_input",y.dataType,y.dims):void 0;y&&xe.push(at);let et=[ke("output",i.dataType,D)];I&&et.push(ke("present_value",i.dataType,M));let tt=[{name:"M",type:"u32"},{name:"K",type:"u32"},{name:"N",type:"u32"},{name:"num_heads",type:"u32"},{name:"head_size",type:"u32"},{name:"v_hidden_size",type:"u32"},{name:"past_sequence_length",type:"u32"},{name:"kv_sequence_length",type:"u32"},{name:"n_reps",type:"u32"}];return`
  const TILE_SIZE = ${L}u;
  var<workgroup> tileQ: array<${fe.type.value}, ${L*L}>;
  var<workgroup> tileV: array<${fe.type.value}, ${L*L}>;
  ${de.registerUniforms(tt).declareVariables(...xe,...et)}
  ${de.mainStart([L,L,1])}
   let headIdx = workgroup_id.z % uniforms.num_heads;
   let batchIdx = workgroup_id.z / uniforms.num_heads;
   let kvHeadIdx = ${$===1?"headIdx":"headIdx / uniforms.n_reps"};
   let kv_num_heads = ${$===1?"uniforms.num_heads":"uniforms.num_heads / uniforms.n_reps"};
   let m = global_id.y;
   let n = global_id.x;
   let sequence_length = uniforms.M;
   var total_sequence_length = uniforms.K;
   ${Sn(De,at,!0)}
   let offsetA = workgroup_id.z * uniforms.M * uniforms.K + m * uniforms.K;
   let absKvHeadIdx = batchIdx * kv_num_heads + kvHeadIdx; // kvHeadIdx is relative to the batch
   ${V&&I?"let pastValueOffset = absKvHeadIdx * uniforms.N * uniforms.past_sequence_length + n;":""};
   let vOffset = absKvHeadIdx * uniforms.N * uniforms.kv_sequence_length + n;
   ${I?"let presentValueOffset = absKvHeadIdx * uniforms.N * uniforms.K + n;":""}
   var value = ${fe.type.storage}(0);
   for (var w: u32 = 0u; w < uniforms.K; w += TILE_SIZE) {
      if (m < uniforms.M && w + local_id.x < uniforms.K) {
        tileQ[TILE_SIZE * local_id.y + local_id.x] = probs[offsetA + w + local_id.x];
      }
      if (n < uniforms.N && w + local_id.y < uniforms.K) {
        var idx = TILE_SIZE * local_id.y + local_id.x;
        ${V&&I?`
        if (w + local_id.y < past_sequence_length) {
          tileV[idx] = past_value[pastValueOffset + (w + local_id.y) * uniforms.N];
        } else if (w + local_id.y - past_sequence_length < uniforms.kv_sequence_length) {
          tileV[idx] = v[vOffset + (w + local_id.y - past_sequence_length) * uniforms.N];
        }
      `:`
            if (w + local_id.y < uniforms.kv_sequence_length) {
              tileV[idx] = v[vOffset + (w + local_id.y) * uniforms.N];
            }`}
        ${I?`
            if (w + local_id.y < present_sequence_length) {
          present_value[presentValueOffset + (w + local_id.y) * uniforms.N] = tileV[idx];
        }`:""}
      }
     workgroupBarrier();
     for (var k: u32 = 0u; k < TILE_SIZE && w+k < total_sequence_length; k++) {
       value += tileQ[TILE_SIZE * local_id.y + k] * tileV[TILE_SIZE * k + local_id.x];
     }
     workgroupBarrier();
   }

   // we need to transpose output from BNSH_v to BSND_v
   if (m < uniforms.M && n < uniforms.N) {
     let outputIdx = batchIdx * uniforms.M * uniforms.v_hidden_size + m * uniforms.v_hidden_size
       + headIdx * uniforms.N + n;
     output[outputIdx] = value;
   }
  }`};return{name:"AttentionScore",shaderCache:{hint:`${u!==void 0};${t}`,inputDependencies:J},getRunData:()=>({outputs:Y,dispatchGroup:Z,programUniforms:W}),getShaderSource:se}},Za=(t,i,n,u,l,c,h,y,b,$,k=void 0,I=void 0)=>{let O=Math.min(t.outputCount,1+(h?1:0)+(y?1:0)),M=O>1?h:void 0,D=O>1?y:void 0,L=O>1?$.pastSequenceLength:0,Z=L+$.kvSequenceLength,W=b&&he.size(b.dims)>0?b:void 0,V=[i,n];M&&he.size(M.dims)>0&&V.push(M),W&&V.push(W),k&&V.push(k),I&&V.push(I);let J=t.compute(Ms(O,i,n,M,W,$,L,k,I),{inputs:V,outputs:O>1?[-1,1]:[-1]})[0];t.compute(Bs(J,$.batchSize,$.numHeads,L,$.sequenceLength,Z,k,I),{inputs:k&&I?[J,k,I]:[J],outputs:[]});let Y=[J,u];D&&he.size(D.dims)>0&&Y.push(D),k&&Y.push(k),I&&Y.push(I),t.compute(Ds(O,J,u,D,$,L,k,I),{inputs:Y,outputs:O>1?[0,2]:[0]})},Ns=(t,i)=>{let n=[i.batchSize,i.numHeads,i.sequenceLength,i.headSize],u=i.sequenceLength,l=i.inputHiddenSize,c=i.headSize,h=12,y={x:Math.ceil(i.headSize/h),y:Math.ceil(i.sequenceLength/h),z:i.batchSize*i.numHeads},b=[t.inputs[0],t.inputs[1],t.inputs[2]],$=[{type:12,data:u},{type:12,data:l},{type:12,data:c},{type:12,data:i.numHeads},{type:12,data:i.headSize},{type:12,data:i.hiddenSize},{type:12,data:i.hiddenSize+i.hiddenSize+i.vHiddenSize}],k=I=>{let O=ke("output_q",b[0].dataType,n),M=ke("output_k",b[0].dataType,n),D=ke("output_v",b[0].dataType,n),L=oe("input",b[0].dataType,b[0].dims),Z=oe("weight",b[1].dataType,b[1].dims),W=oe("bias",b[2].dataType,b[2].dims),V=L.type.storage,J=[{name:"M",type:"u32"},{name:"K",type:"u32"},{name:"N",type:"u32"},{name:"num_heads",type:"u32"},{name:"head_size",type:"u32"},{name:"hidden_size",type:"u32"},{name:"ldb",type:"u32"}];return`
  const TILE_SIZE = ${h}u;
  var<workgroup> tileInput: array<${V}, ${h*h}>;
  var<workgroup> tileWeightQ: array<${V}, ${h*h}>;
  var<workgroup> tileWeightK: array<${V}, ${h*h}>;
  var<workgroup> tileWeightV: array<${V}, ${h*h}>;
  ${I.registerUniforms(J).declareVariables(L,Z,W,O,M,D)}
  ${I.mainStart([h,h,1])}
    let batchIndex = workgroup_id.z / uniforms.num_heads;
    let headNumber = workgroup_id.z % uniforms.num_heads;
    let m = global_id.y;
    let n = global_id.x;

    let inputOffset = batchIndex * (uniforms.M * uniforms.K) + m * uniforms.K;
    let biasOffsetQ = headNumber * uniforms.head_size;
    let biasOffsetK = uniforms.hidden_size + biasOffsetQ;
    let biasOffsetV = uniforms.hidden_size + biasOffsetK;

    var valueQ = ${V}(0);
    var valueK = ${V}(0);
    var valueV = ${V}(0);
    for (var w: u32 = 0u; w < uniforms.K; w += TILE_SIZE) {
      if (m < uniforms.M && w + local_id.x < uniforms.K) {
        tileInput[TILE_SIZE * local_id.y + local_id.x] = input[inputOffset + w + local_id.x];
      }
      if (n < uniforms.N && w + local_id.y < uniforms.K) {
        let offset = n + (w + local_id.y) * uniforms.ldb;
        tileWeightQ[TILE_SIZE * local_id.y + local_id.x] = weight[biasOffsetQ + offset];
        tileWeightK[TILE_SIZE * local_id.y + local_id.x] = weight[biasOffsetK + offset];
        tileWeightV[TILE_SIZE * local_id.y + local_id.x] = weight[biasOffsetV + offset];
      }
      workgroupBarrier();
      for (var k: u32 = 0u; k<TILE_SIZE && w+k < uniforms.K; k++) {
        let inputTileOffset = TILE_SIZE * local_id.y + k;
        let weightTileOffset = TILE_SIZE * k + local_id.x;
        valueQ += tileInput[inputTileOffset] * tileWeightQ[weightTileOffset];
        valueK += tileInput[inputTileOffset] * tileWeightK[weightTileOffset];
        valueV += tileInput[inputTileOffset] * tileWeightV[weightTileOffset];
      }

      workgroupBarrier();
    }

    let headOffset = (m * uniforms.N + n) % uniforms.head_size;
    valueQ += bias[headOffset + biasOffsetQ];
    valueK += bias[headOffset + biasOffsetK];
    valueV += bias[headOffset + biasOffsetV];

    let offset = workgroup_id.z * uniforms.M * uniforms.N;
    if (m < uniforms.M && n < uniforms.N) {
      let outputIdx = offset + m * uniforms.N + n;
      output_q[outputIdx] = valueQ;
      output_k[outputIdx] = valueK;
      output_v[outputIdx] = valueV;
    }
  }`};return t.compute({name:"AttentionPrepare",shaderCache:{inputDependencies:["type","type","type"]},getRunData:()=>({outputs:[{dims:n,dataType:t.inputs[0].dataType,gpuDataType:0},{dims:n,dataType:t.inputs[0].dataType,gpuDataType:0},{dims:n,dataType:t.inputs[0].dataType,gpuDataType:0}],dispatchGroup:y,programUniforms:$}),getShaderSource:k},{inputs:b,outputs:[-1,-1,-1]})},Ps=(t,i)=>{let n=Rs(t.inputs,i),[u,l,c]=Ns(t,n);return Za(t,u,l,c,t.inputs[4],void 0,void 0,void 0,t.inputs[5],n)}}),Us,Ls,qs,Vs,Zo=m(()=>{Kt(),it(),Xe(),j(),Ke(),Us=(t,i)=>{if(!t||t.length!==5)throw new Error("BatchNormalization requires 5 inputs");let n=(u,l,c)=>{let h=l.length;if(h!==u.length)throw new Error(`${c}: num dimensions != ${h}`);l.forEach((y,b)=>{if(y!==u[b])throw new Error(`${c}: dim[${b}] do not match`)})};if(t[0].dims.length>1){let u=i.format==="NHWC"?i.spatial?t[0].dims.slice(-1):t[0].dims.slice(-1).concat(t[0].dims.slice(1,t[0].dims.length-1)):t[0].dims.slice(1,i.spatial?2:void 0);n(t[1].dims,u,"Invalid input scale"),n(t[2].dims,u,"Invalid input B"),n(t[3].dims,u,"Invalid input mean"),n(t[4].dims,u,"Invalid input var")}else n(t[1].dims,[1],"Invalid input scale"),n(t[2].dims,[1],"Invalid input B"),n(t[3].dims,[1],"Invalid input mean"),n(t[4].dims,[1],"Invalid input var")},Ls=(t,i)=>{let{epsilon:n,spatial:u,format:l}=i,c=t[0].dims,h=u?le(c[c.length-1]):1,y=l==="NHWC"&&c.length>1?h:1,b=he.size(c)/h,$=u,k=$?c.length:c,I=oe("x",t[0].dataType,t[0].dims,h),O=oe("scale",t[1].dataType,t[1].dims,y),M=oe("bias",t[2].dataType,t[2].dims,y),D=oe("inputMean",t[3].dataType,t[3].dims,y),L=oe("inputVar",t[4].dataType,t[4].dims,y),Z=ke("y",t[0].dataType,k,h),W=()=>{let J="";if(u)J=`let cOffset = ${c.length===1?"0u":l==="NHWC"?`outputIndices[${c.length-1}] / ${h}`:"outputIndices[1]"};`;else if(l==="NCHW")J=`
            ${Z.indicesSet("outputIndices","0","0")}
            let cOffset = ${Z.indicesToOffset("outputIndices")};`;else{J=`var cIndices = ${O.type.indices}(0);
                       cIndices[0] = outputIndices[${c.length-1}];`;for(let Y=1;Y<O.rank;Y++)J+=`cIndices[${Y}] = outputIndices[${Y}];`;J+=`let cOffset = ${O.indicesToOffset("cIndices")};`}return J},V=J=>`
  const epsilon = ${n};
  ${J.registerUniform("outputSize","u32").declareVariables(I,O,M,D,L,Z)}
  ${J.mainStart()}
  ${J.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
    var outputIndices = ${Z.offsetToIndices(`global_idx * ${h}`)};
    ${W()}
    let scale = ${O.getByOffset("cOffset")};
    let bias = ${M.getByOffset("cOffset")};
    let inputMean = ${D.getByOffset("cOffset")};
    let inputVar = ${L.getByOffset("cOffset")};
    let x = ${I.getByOffset("global_idx")};
    let value = (x - inputMean) * inverseSqrt(inputVar + epsilon) * scale + bias;
    ${Z.setByOffset("global_idx","value")}
  }`;return{name:"BatchNormalization",shaderCache:{hint:`${i.epsilon}_${i.format}_${u}_${h}`,inputDependencies:$?["rank","type","type","type","type"]:void 0},getShaderSource:V,getRunData:()=>({outputs:[{dims:t[0].dims,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(b/64)},programUniforms:$?[{type:12,data:b},...ie(c)]:[{type:12,data:b}]})}},qs=t=>N(t),Vs=(t,i)=>{let{inputs:n,outputCount:u}=t,l=qs({...i,outputCount:u});if(B.webgpu.validateInputContent&&Us(n,l),i.trainingMode)throw new Error("BatchNormalization trainingMode is not supported yet.");t.compute(Ls(n,l))}}),Ws,Gs,Fs,Qo=m(()=>{Xe(),Ke(),Ws=t=>{if(t[0].dims.length!==3)throw new Error("input should have 3 dimensions");if(![320,640,1280].includes(t[0].dims[2]))throw new Error("number of channels should be 320, 640 or 1280");if(t[1].dims.length!==1)throw new Error("bias is expected to have 1 dimensions");if(t[0].dims[2]!==t[1].dims[0])throw new Error("last dimension of input and bias are not the same")},Gs=t=>{let i=t[0].dims,n=t[0].dims[2],u=he.size(i)/4,l=t[0].dataType,c=oe("input",l,i,4),h=oe("bias",l,[n],4),y=oe("residual",l,i,4),b=ke("output",l,i,4);return{name:"BiasAdd",getRunData:()=>({outputs:[{dims:i,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(u/64)}}),getShaderSource:$=>`
  const channels = ${n}u / 4;
  ${$.declareVariables(c,h,y,b)}

  ${$.mainStart()}
    ${$.guardAgainstOutOfBoundsWorkgroupSizes(u)}
    let value = ${c.getByOffset("global_idx")}
      + ${h.getByOffset("global_idx % channels")} + ${y.getByOffset("global_idx")};
    ${b.setByOffset("global_idx","value")}
  }`}},Fs=t=>{Ws(t.inputs),t.compute(Gs(t.inputs))}}),Hs,Tt,js,Ks,Zs,Qs,Xs,Ys,Js,eo,to,ro,io,ao,_i,Xo,ma,Yo,ds,Jo,eu,tu,ru,iu,au,nu,su,ou,uu,lu,du,pu,cu,hu,fu,no,mu,so,oo,gu,yu,_u,wu,bu,$u,uo=m(()=>{it(),Xe(),j(),Ke(),Hs=(t,i,n,u,l,c,h)=>{let y=Math.ceil(i/4),b="";typeof l=="string"?b=`${l}(a)`:b=l("a");let $=oe("inputData",n,[y],4),k=ke("outputData",u,[y],4),I=[{name:"vec_size",type:"u32"}];return h&&I.push(...h),`
      ${t.registerUniforms(I).declareVariables($,k)}

  ${c??""}

  ${t.mainStart()}
    ${t.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}

    let a = ${$.getByOffset("global_idx")};
    ${k.setByOffset("global_idx",b)}
  }`},Tt=(t,i,n,u,l,c=t.dataType,h,y)=>{let b=[{type:12,data:Math.ceil(he.size(t.dims)/4)}];return h&&b.push(...h),{name:i,shaderCache:{hint:l,inputDependencies:["type"]},getShaderSource:$=>Hs($,he.size(t.dims),t.dataType,c,n,u,y),getRunData:$=>({outputs:[{dims:t.dims,dataType:c}],dispatchGroup:{x:Math.ceil(he.size($[0].dims)/64/4)},programUniforms:b})}},js=t=>{t.compute(Tt(t.inputs[0],"Abs","abs"))},Ks=t=>{t.compute(Tt(t.inputs[0],"Acos","acos"))},Zs=t=>{t.compute(Tt(t.inputs[0],"Acosh","acosh"))},Qs=t=>{t.compute(Tt(t.inputs[0],"Asin","asin"))},Xs=t=>{t.compute(Tt(t.inputs[0],"Asinh","asinh"))},Ys=t=>{t.compute(Tt(t.inputs[0],"Atan","atan"))},Js=t=>{t.compute(Tt(t.inputs[0],"Atanh","atanh"))},eo=t=>N(t),to=(t,i)=>{let n;switch(i.to){case 10:n="vec4<f16>";break;case 1:n="vec4<f32>";break;case 12:n="vec4<u32>";break;case 6:n="vec4<i32>";break;case 9:n="vec4<bool>";break;default:throw new RangeError(`not supported type (specified in attribute 'to' from 'Cast' operator): ${i.to}`)}t.compute(Tt(t.inputs[0],"Cast",n,void 0,i.cacheKey,i.to))},ro=t=>{let i,n,u=t.length>=2&&t[1].data!==0,l=t.length>=3&&t[2].data!==0;switch(t[0].dataType){case 1:i=u?t[1].getFloat32Array()[0]:-34028234663852886e22,n=l?t[2].getFloat32Array()[0]:34028234663852886e22;break;case 10:i=u?t[1].getUint16Array()[0]:64511,n=l?t[2].getUint16Array()[0]:31743;break;default:throw new Error("Unsupport data type")}return N({min:i,max:n})},io=(t,i)=>{let n=i||ro(t.inputs),u=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"Clip",l=>`clamp(${l}, vec4<${u}>(uniforms.min), vec4<${u}>(uniforms.max))`,void 0,n.cacheKey,void 0,[{type:t.inputs[0].dataType,data:n.min},{type:t.inputs[0].dataType,data:n.max}],[{name:"min",type:u},{name:"max",type:u}]),{inputs:[0]})},ao=t=>{t.compute(Tt(t.inputs[0],"Ceil","ceil"))},_i=t=>{t.compute(Tt(t.inputs[0],"Cos","cos"))},Xo=t=>{t.compute(Tt(t.inputs[0],"Cosh","cosh"))},ma=t=>N(t),Yo=(t,i)=>{let n=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"Elu",u=>`elu_vf32(${u})`,`
  const elu_alpha_ = ${n}(${i.alpha});

  fn elu_f32(a: ${n}) -> ${n} {
  return select((exp(a) - 1.0) * elu_alpha_, a, a >= 0.0);
  }

  fn elu_vf32(v: vec4<${n}>) -> vec4<${n}> {
  return vec4(elu_f32(v.x), elu_f32(v.y), elu_f32(v.z), elu_f32(v.w));
  }`,i.cacheKey))},ds=(t="f32")=>`
const r0: ${t} = 0.3275911;
const r1: ${t} = 0.254829592;
const r2: ${t} = -0.284496736;
const r3: ${t} = 1.421413741;
const r4: ${t} = -1.453152027;
const r5: ${t} = 1.061405429;

fn erf_vf32(v: vec4<${t}>) -> vec4<${t}> {
  let absv = abs(v);
  let x = 1.0 / (1.0 + r0 * absv);
  return sign(v) * (1.0 - ((((r5 * x + r4) * x + r3) * x + r2) * x + r1) * x * exp(-absv * absv));
}`,Jo=t=>{let i=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"Erf",n=>`erf_vf32(${n})`,ds(i)))},eu=t=>{t.compute(Tt(t.inputs[0],"Exp","exp"))},tu=t=>{t.compute(Tt(t.inputs[0],"Floor","floor"))},ru=t=>{let i=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"Gelu",n=>`0.5 * ${n} * (1.0 + erf_vf32(${n} * 0.7071067811865475))`,ds(i)))},iu=(t,i)=>{let n=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"LeakyRelu",u=>`select(leaky_relu_alpha_ * ${u}, ${u}, ${u} >= vec4<${n}>(0.0))`,`const leaky_relu_alpha_ = ${n}(${i.alpha});`,i.cacheKey))},au=t=>{t.compute(Tt(t.inputs[0],"Not",i=>`!${i}`))},nu=t=>{t.compute(Tt(t.inputs[0],"Neg",i=>`-${i}`))},su=t=>{t.compute(Tt(t.inputs[0],"Reciprocal",i=>`1.0/${i}`))},ou=t=>{let i=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"Relu",n=>`select(vec4<${i}>(0.0), ${n}, ${n} > vec4<${i}>(0.0))`))},uu=t=>{t.compute(Tt(t.inputs[0],"Sigmoid",i=>`(1.0 / (1.0 + exp(-${i})))`))},lu=t=>N(t),du=(t,i)=>{let n=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"HardSigmoid",u=>`max(vec4<${n}>(0.0), min(vec4<${n}>(1.0), ${i.alpha} * ${u} + vec4<${n}>(${i.beta})))`,void 0,i.cacheKey))},pu=t=>{t.compute(Tt(t.inputs[0],"Sin","sin"))},cu=t=>{t.compute(Tt(t.inputs[0],"Sinh","sinh"))},hu=t=>{t.compute(Tt(t.inputs[0],"Sqrt","sqrt"))},fu=t=>{t.compute(Tt(t.inputs[0],"Tan","tan"))},no=t=>`sign(${t}) * (1 - exp(-2 * abs(${t}))) / (1 + exp(-2 * abs(${t})))`,mu=t=>{t.compute(Tt(t.inputs[0],"Tanh",no))},so=(t="f32")=>`
const fast_gelu_a: ${t} = 0.5;
const fast_gelu_b: ${t} = 0.7978845608028654;
const fast_gelu_c: ${t} = 0.035677408136300125;

fn tanh_v(v: vec4<${t}>) -> vec4<${t}> {
  return ${no("v")};
}
`,oo=t=>`(fast_gelu_a + fast_gelu_a * tanh_v(${t} * (fast_gelu_c * ${t} * ${t} + fast_gelu_b))) * ${t}`,gu=t=>{let i=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"FastGelu",oo,so(i),void 0,t.inputs[0].dataType))},yu=(t,i)=>{let n=re(t.inputs[0].dataType);return t.compute(Tt(t.inputs[0],"ThresholdedRelu",u=>`select(vec4<${n}>(0.0), ${u}, ${u} > thresholded_relu_alpha_)`,`const thresholded_relu_alpha_ = vec4<${n}>(${i.alpha});`,i.cacheKey)),0},_u=t=>{t.compute(Tt(t.inputs[0],"Log","log"))},wu=(t,i)=>`
const alpha = vec4<${t}>(${i});
const one = ${t}(1.0);
const zero = ${t}(0.0);

fn quick_gelu_impl(x: vec4<${t}>) -> vec4<${t}> {
  let v = x *alpha;
  var x1 : vec4<${t}>;
  for (var i = 0; i < 4; i = i + 1) {
    if (v[i] >= zero) {
      x1[i] = one / (one + exp(-v[i]));
    } else {
      x1[i] = one - one / (one + exp(v[i]));
    }
  }
  return x * x1;
}
`,bu=t=>`quick_gelu_impl(${t})`,$u=(t,i)=>{let n=re(t.inputs[0].dataType);t.compute(Tt(t.inputs[0],"QuickGelu",bu,wu(n,i.alpha),i.cacheKey,t.inputs[0].dataType))}}),vu,xu,Su,pl=m(()=>{Xe(),Ke(),uo(),vu=t=>{if(t[0].dims.length!==3)throw new Error("input should have 3 dimensions");if(![2560,5120,10240].includes(t[0].dims[2]))throw new Error("hidden state should be 2560, 5120 or 10240");if(t[1].dims.length!==1)throw new Error("bias is expected to have 1 dimensions");if(t[0].dims[2]!==t[1].dims[0])throw new Error("last dimension of input and bias are not the same")},xu=t=>{let i=t[0].dims.slice();i[2]=i[2]/2;let n=oe("input",t[0].dataType,t[0].dims,4),u=oe("bias",t[0].dataType,[t[0].dims[2]],4),l=ke("output",t[0].dataType,i,4),c=he.size(i)/4,h=ue(t[0].dataType);return{name:"BiasSplitGelu",getRunData:()=>({outputs:[{dims:i,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(c/64)}}),getShaderSource:y=>`
  const M_SQRT2 = sqrt(2.0);
  const halfChannels = ${t[0].dims[2]/4/2}u;

  ${y.declareVariables(n,u,l)}

  ${ds(h)}

  ${y.mainStart()}
    ${y.guardAgainstOutOfBoundsWorkgroupSizes(c)}
    let biasIdx = global_idx % halfChannels;
    let batchIndex = global_idx / halfChannels;
    let inputOffset = biasIdx + batchIndex * halfChannels * 2;
    let valueLeft = input[inputOffset] + bias[biasIdx];
    let valueRight = input[inputOffset + halfChannels] + bias[biasIdx + halfChannels];
    let geluRight = valueRight * 0.5 * (erf_vf32(valueRight / M_SQRT2) + 1);

    ${l.setByOffset("global_idx","valueLeft * geluRight")}
  }`}},Su=t=>{vu(t.inputs),t.compute(xu(t.inputs))}}),Tu,ku,ri,Eu,Iu,zu,Tn,lo,po,f,w,E,S,q=m(()=>{it(),Xe(),Ke(),Tu=(t,i,n,u,l,c,h,y,b,$,k,I)=>{let O,M;typeof y=="string"?O=M=(V,J)=>`${y}((${V}),(${J}))`:typeof y=="function"?O=M=y:(O=y.scalar,M=y.vector);let D=ke("outputData",k,u.length,4),L=oe("aData",b,i.length,4),Z=oe("bData",$,n.length,4),W;if(l)if(c){let V=he.size(i)===1,J=he.size(n)===1,Y=i.length>0&&i[i.length-1]%4===0,se=n.length>0&&n[n.length-1]%4===0;V||J?W=D.setByOffset("global_idx",M(V?`${L.type.value}(${L.getByOffset("0")}.x)`:L.getByOffset("global_idx"),J?`${Z.type.value}(${Z.getByOffset("0")}.x)`:Z.getByOffset("global_idx"))):W=`
            let outputIndices = ${D.offsetToIndices("global_idx * 4u")};
            let offsetA = ${L.broadcastedIndicesToOffset("outputIndices",D)};
            let offsetB = ${Z.broadcastedIndicesToOffset("outputIndices",D)};
            ${D.setByOffset("global_idx",M(h||Y?L.getByOffset("offsetA / 4u"):`${L.type.value}(${L.getByOffset("offsetA / 4u")}[offsetA % 4u])`,h||se?Z.getByOffset("offsetB / 4u"):`${Z.type.value}(${Z.getByOffset("offsetB / 4u")}[offsetB % 4u])`))}
          `}else W=D.setByOffset("global_idx",M(L.getByOffset("global_idx"),Z.getByOffset("global_idx")));else{if(!c)throw new Error("no necessary to use scalar implementation for element-wise binary op implementation.");let V=(J,Y,se="")=>{let de=`aData[indexA${Y}][componentA${Y}]`,fe=`bData[indexB${Y}][componentB${Y}]`;return`
            let outputIndices${Y} = ${D.offsetToIndices(`global_idx * 4u + ${Y}u`)};
            let offsetA${Y} = ${L.broadcastedIndicesToOffset(`outputIndices${Y}`,D)};
            let offsetB${Y} = ${Z.broadcastedIndicesToOffset(`outputIndices${Y}`,D)};
            let indexA${Y} = offsetA${Y} / 4u;
            let indexB${Y} = offsetB${Y} / 4u;
            let componentA${Y} = offsetA${Y} % 4u;
            let componentB${Y} = offsetB${Y} % 4u;
            ${J}[${Y}] = ${se}(${O(de,fe)});
          `};k===9?W=`
            var data = vec4<u32>(0);
            ${V("data",0,"u32")}
            ${V("data",1,"u32")}
            ${V("data",2,"u32")}
            ${V("data",3,"u32")}
            outputData[global_idx] = dot(vec4<u32>(0x1, 0x100, 0x10000, 0x1000000), vec4<u32>(data));`:W=`
            ${V("outputData[global_idx]",0)}
            ${V("outputData[global_idx]",1)}
            ${V("outputData[global_idx]",2)}
            ${V("outputData[global_idx]",3)}
          `}return`
        ${t.registerUniform("vec_size","u32").declareVariables(L,Z,D)}

        ${I??""}

        ${t.mainStart()}
        ${t.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}
        ${W}
      }`},ku=(t,i,n,u,l,c,h=n.dataType)=>{let y=n.dims.map(Number),b=u.dims.map(Number),$=!he.areEqual(y,b),k=y,I=he.size(y),O=!1,M=!1,D=[$];if($){let L=hi.calcShape(y,b,!1);if(!L)throw new Error("Can't perform binary op on the given tensors");k=L.slice(),I=he.size(k);let Z=he.size(y)===1,W=he.size(b)===1,V=y.length>0&&y[y.length-1]%4===0,J=b.length>0&&b[b.length-1]%4===0;D.push(Z),D.push(W),D.push(V),D.push(J);let Y=1;for(let se=1;se<k.length;se++){let de=y[y.length-se],fe=b[b.length-se];if(de===fe)Y*=de;else break}Y%4===0?(M=!0,O=!0):(Z||W||V||J)&&(O=!0)}else O=!0;return D.push(O),{name:t,shaderCache:{hint:i+D.map(L=>L.toString()).join("_"),inputDependencies:["rank","rank"]},getShaderSource:L=>Tu(L,y,b,k,O,$,M,l,n.dataType,u.dataType,h,c),getRunData:()=>({outputs:[{dims:k,dataType:h}],dispatchGroup:{x:Math.ceil(I/64/4)},programUniforms:[{type:12,data:Math.ceil(he.size(k)/4)},...ie(y,b,k)]})}},ri=(t,i,n,u,l,c)=>{t.compute(ku(i,l??"",t.inputs[0],t.inputs[1],n,u,c))},Eu=t=>{ri(t,"Add",(i,n)=>`${i}+${n}`)},Iu=t=>{ri(t,"Div",(i,n)=>`${i}/${n}`)},zu=t=>{ri(t,"Equal",{scalar:(i,n)=>`u32(${i}==${n})`,vector:(i,n)=>`vec4<u32>(${i}==${n})`},void 0,void 0,9)},Tn=t=>{ri(t,"Mul",(i,n)=>`${i}*${n}`)},lo=t=>{let i=oe("input",t.inputs[0].dataType,t.inputs[0].dims).type.value;ri(t,"Pow",{scalar:(n,u)=>`pow_custom(${n},${u})`,vector:(n,u)=>`pow_vector_custom(${n},${u})`},`
    fn pow_custom(a : ${i}, b : ${i}) -> ${i} {
      if (b == ${i}(0.0)) {
        return ${i}(1.0);
      } else if (a < ${i}(0.0) && f32(b) != floor(f32(b))) {
        return ${i}(pow(f32(a), f32(b))); // NaN
      }
      return select(sign(a), ${i}(1.0), round(f32(abs(b) % ${i}(2.0))) != 1.0) * ${i}(${i==="i32"?"round":""}(pow(f32(abs(a)), f32(b))));
    }
    fn pow_vector_custom(a : vec4<${i}>, b : vec4<${i}>) -> vec4<${i}> {
      // TODO: implement vectorized pow
      return vec4<${i}>(pow_custom(a.x, b.x), pow_custom(a.y, b.y), pow_custom(a.z, b.z), pow_custom(a.w, b.w));
    }
      `)},po=t=>{ri(t,"Sub",(i,n)=>`${i}-${n}`)},f=t=>{ri(t,"Greater",{scalar:(i,n)=>`u32(${i}>${n})`,vector:(i,n)=>`vec4<u32>(${i}>${n})`},void 0,void 0,9)},w=t=>{ri(t,"Less",{scalar:(i,n)=>`u32(${i}<${n})`,vector:(i,n)=>`vec4<u32>(${i}<${n})`},void 0,void 0,9)},E=t=>{ri(t,"GreaterOrEqual",{scalar:(i,n)=>`u32(${i}>=${n})`,vector:(i,n)=>`vec4<u32>(${i}>=${n})`},void 0,void 0,9)},S=t=>{ri(t,"LessOrEqual",{scalar:(i,n)=>`u32(${i}<=${n})`,vector:(i,n)=>`vec4<u32>(${i}<=${n})`},void 0,void 0,9)}}),Q,ne,ye,Oe,Le,pt,kt=m(()=>{it(),Xe(),j(),Ke(),Q=(t,i)=>{if(!t||t.length<1)throw new Error("too few inputs");let n=0,u=t[n],l=u.dataType,c=u.dims.length;t.forEach((h,y)=>{if(y!==n){if(h.dataType!==l)throw new Error("input tensors should be one type");if(h.dims.length!==c)throw new Error("input tensors should have the same shape");h.dims.forEach((b,$)=>{if($!==i&&b!==u.dims[$])throw new Error("non concat dimensions must match")})}})},ne=(t,i)=>`
  fn calculateInputIndex(index: u32) -> u32 {
    let sizeInConcatAxis = array<u32, ${t}u>(${i});
    for (var i: u32 = 0u; i < ${t}; i += 1u ) {
      if (index < sizeInConcatAxis[i]) {
        return i;
      }
    }
    return ${t}u;
  }`,ye=(t,i)=>{let n=t.length,u=[];for(let l=0;l<n;++l){let c=i.setByOffset("global_idx",t[l].getByIndices("indices"));n===1?u.push(c):l===0?u.push(`if (inputIndex == ${l}u) { ${c} }`):l===n-1?u.push(`else { ${c} }`):u.push(`else if (inputIndex == ${l}) { ${c} }`)}return u.join(`
`)},Oe=(t,i,n,u)=>{let l=he.size(n),c=new Array(t.length),h=new Array(t.length),y=0,b=[],$=[],k=[{type:12,data:l}];for(let L=0;L<t.length;++L)y+=t[L].dims[i],c[L]=y,$.push(t[L].dims.length),h[L]=oe(`input${L}`,u,$[L]),b.push("rank"),k.push({type:12,data:c[L]});for(let L=0;L<t.length;++L)k.push(...ie(t[L].dims));k.push(...ie(n));let I=ke("output",u,n.length),O=I.indicesGet("indices",i),M=Array.from(Array(c.length).keys()).map(L=>`uniforms.sizeInConcatAxis${L}`).join(","),D=L=>`

  ${(()=>{L.registerUniform("outputSize","u32");for(let Z=0;Z<t.length;Z++)L.registerUniform(`sizeInConcatAxis${Z}`,"u32");return L.declareVariables(...h,I)})()}

  ${ne(c.length,M)}

  ${L.mainStart()}
    ${L.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}

    var indices = ${I.offsetToIndices("global_idx")};

    let inputIndex = calculateInputIndex(${O});
    if (inputIndex != 0u) {
      let sizeInConcatAxis = array<u32, ${c.length}u>(${M});
      ${O} -= sizeInConcatAxis[inputIndex - 1u];
    }

    ${ye(h,I)}
  }`;return{name:"Concat",shaderCache:{hint:`${i}`,inputDependencies:b},getRunData:()=>({outputs:[{dims:n,dataType:u}],dispatchGroup:{x:Math.ceil(l/64)},programUniforms:k}),getShaderSource:D}},Le=(t,i)=>{let n=t.inputs,u=n[0].dims,l=he.normalizeAxis(i.axis,u.length);Q(n,l);let c=u.slice();c[l]=n.reduce((y,b)=>y+(b.dims.length>l?b.dims[l]:0),0);let h=n.filter(y=>he.size(y.dims)>0);t.compute(Oe(h,l,c,n[0].dataType),{inputs:h})},pt=t=>N({axis:t.axis})}),Et,At,Ar,kn,ga=m(()=>{it(),Xe(),Et=(t,i,n="f32")=>{switch(t.activation){case"Relu":return`value = max(value, ${i}(0.0));`;case"Sigmoid":return`value = (${i}(1.0) / (${i}(1.0) + exp(-value)));`;case"Clip":return`value = clamp(value, ${i}(${n}(uniforms.clip_min)), ${i}(${n}(uniforms.clip_max)));`;case"HardSigmoid":return`value = max(${i}(0.0), min(${i}(1.0), ${n}(uniforms.alpha) * value + ${n}(uniforms.beta)));`;case"LeakyRelu":return`value = select(${n}(uniforms.alpha) * value, value, value >= ${i}(0.0));`;case"Tanh":return`let e2x = exp(-2.0 * abs(value));
              value = sign(value) * (1.0 - e2x) / (1.0 + e2x);
        `;case"":return"";default:throw new Error(`Unsupported activation ${t.activation}`)}},At=(t,i)=>{t.activation==="Clip"?i.push({type:1,data:t.clipMax},{type:1,data:t.clipMin}):t.activation==="HardSigmoid"?i.push({type:1,data:t.alpha},{type:1,data:t.beta}):t.activation==="LeakyRelu"&&i.push({type:1,data:t.alpha})},Ar=(t,i)=>{t.activation==="Clip"?i.push({name:"clip_max",type:"f32"},{name:"clip_min",type:"f32"}):t.activation==="HardSigmoid"?i.push({name:"alpha",type:"f32"},{name:"beta",type:"f32"}):t.activation==="LeakyRelu"&&i.push({name:"alpha",type:"f32"})},kn=t=>{let i=(t==null?void 0:t.activation)||"";if(i==="HardSigmoid"){let[n,u]=(t==null?void 0:t.activation_params)||[.2,.5];return{activation:i,alpha:n,beta:u}}else if(i==="Clip"){let[n,u]=(t==null?void 0:t.activation_params)||[mn,zr];return{activation:i,clipMax:u,clipMin:n}}else if(i==="LeakyRelu"){let[n]=(t==null?void 0:t.activation_params)||[.01];return{activation:i,alpha:n}}return{activation:i}}}),nr,Qp,cl=m(()=>{nr=(t,i)=>{switch(t){case 1:return i;case 2:return`vec2<${i}>`;case 3:return`vec3<${i}>`;case 4:return`vec4<${i}>`;default:throw new Error(`${t}-component is not supported.`)}},Qp=t=>`
      ${t?"value = value + getBiasByOutputCoords(coords);":""}
      `}),Xp,kv=m(()=>{Xp=t=>`
fn getIndexFromCoords4D(coords : vec4<i32>, shape : vec4<i32>) -> i32 {
  return dot(coords, vec4<i32>(
      shape.y * shape.z * shape.w, shape.z * shape.w, shape.w, 1));
}
fn getOutputIndexFromCoords(coords : vec4<i32>) -> i32 {
  return dot(coords, vec4<i32>(
    i32(${t}.x), i32(${t}.y), i32(${t}.z), 1));
}
`}),co,hl,fl=m(()=>{it(),Xe(),Ke(),ga(),co=(t,i,n,u,l)=>{let c=u-n;return`
      ${Array.from({length:n}).map((h,y)=>`
      if (${ce(i.shape,y,i.rank)} != 1) {
        ${i.indicesSet(t,y,ce(l,y+c,u))}
      } else {
        ${i.indicesSet(t,y,0)}
      }`).join("")}
`},hl=(t,i,n,u,l=!1,c)=>{let h=t[0].dims,y=t[1].dims,b=h[h.length-2],$=y[y.length-1],k=h[h.length-1],I=le($),O=le(k),M=le(b),D=he.size(n)/I/M,L=t.length>2,Z=u?u.slice(0,-2):n.slice(0,-2),W=[he.size(Z),b,$],V=[{type:12,data:D},{type:12,data:b},{type:12,data:$},{type:12,data:k}];At(i,V),V.push(...ie(Z,h,y)),L&&V.push(...ie(t[2].dims)),V.push(...ie(W));let J=Y=>{let se=dt("batch_dims",t[0].dataType,Z.length),de=oe("a",t[0].dataType,h.length,O),fe=oe("b",t[1].dataType,y.length,I),we=ke("output",t[0].dataType,W.length,I),xe=ue(we.type.tensor),De=Et(i,we.type.value,xe),at=[de,fe],et="";if(L){let zt=l?I:1;at.push(oe("bias",t[2].dataType,t[2].dims.length,zt)),et=`${l?`value += bias[col / ${zt}];`:`value += ${we.type.value}(bias[row + i]);`}`}let tt=[{name:"output_size",type:"u32"},{name:"M",type:"u32"},{name:"N",type:"u32"},{name:"K",type:"u32"}];Ar(i,tt);let xt=()=>{let zt=`var a_data: ${de.type.value};`;for(let rt=0;rt<O;rt++)zt+=`
              let b_data${rt} = b[(b_offset + (k + ${rt}) * uniforms.N + col) / ${I}];`;for(let rt=0;rt<M;rt++){zt+=`a_data = a[(a_offset + (row + ${rt}) * uniforms.K + k) / ${O}];`;for(let ot=0;ot<O;ot++)zt+=`
            values[${rt}] = fma(${fe.type.value}(a_data${O===1?"":`[${ot}]`}), b_data${ot}, values[${rt}]);
`}return zt};return`
  ${Y.registerUniforms(tt).registerInternalVariables(se).declareVariables(...at,we)}
  ${Y.mainStart()}
    ${Y.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let col = (global_idx % (uniforms.N / ${I})) * ${I};
    var index1 = global_idx / (uniforms.N / ${I});
    let stride1 = uniforms.M / ${M};
    let row = (index1 % stride1) * ${M};
    let batch = index1 / stride1;

    ${n.length===2?"":`let batch_indices = ${se.offsetToIndices("batch")};`}

    var a_indices: ${de.type.indices};
    ${co("a_indices",de,de.rank-2,se.rank,"batch_indices")}
    ${de.indicesSet("a_indices",de.rank-2,0)}
    ${de.indicesSet("a_indices",de.rank-1,0)}
    let a_offset = ${de.indicesToOffset("a_indices")};

    var b_indices: ${fe.type.indices};
    ${co("b_indices",fe,fe.rank-2,se.rank,"batch_indices")}
    ${fe.indicesSet("b_indices",fe.rank-2,0)}
    ${fe.indicesSet("b_indices",fe.rank-1,0)}
    let b_offset = ${fe.indicesToOffset("b_indices")};
    var values: array<${we.type.value}, ${M}>;
    for (var k: u32 = 0u; k < uniforms.K; k = k + ${O}) {
      ${xt()}
    }
    for (var i = 0u; i < ${M}u; i++) {
      var value = values[i];
      ${et}
      ${De}
      let cur_indices = ${we.type.indices}(batch, row + i, col);
      let offset = ${we.indicesToOffset("cur_indices")};
      ${we.setByOffset(`offset / ${I}`,"value")};
    }
  }
  `};return{name:"MatMulNaive",shaderCache:{hint:`${i.activation};${I};${O};${M};${l}`,inputDependencies:L?["rank","rank","rank"]:["rank","rank"]},getRunData:()=>({outputs:[{dims:c?c(n):n,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(D/64)},programUniforms:V}),getShaderSource:J}}}),Yp,Jp,ml,gl,ec,yl,tc,Cu,_l=m(()=>{it(),Xe(),Ke(),ga(),fl(),cl(),Yp=(t,i)=>t?`
        mm_Asub[inputRow][inputCol] = mm_readA(batch,
          kStart + inputRow,
          globalRowStart / innerElementSize + inputCol${i?", batchIndices":""});
        `:`
        mm_Asub[inputRow][inputCol] = mm_readA(batch,
          globalRow + innerRow,
          kStart / innerElementSize + inputCol${i?", batchIndices":""});
        `,Jp=(t,i)=>t?`
        let ACached0 = mm_Asub[k * innerElementSize][localRow];
        let ACached1 = mm_Asub[k * innerElementSize + 1][localRow];
        let ACached2 = mm_Asub[k * innerElementSize + 2][localRow];
        ${i===3?"":"let ACached3 = mm_Asub[k * innerElementSize + 3][localRow];"}
        for (var i = 0; i < rowPerThread; i = i + 1) {
          acc[i] = BCached0 * ACached0[i] + acc[i];
          acc[i] = BCached1 * ACached1[i] + acc[i];
          acc[i] = BCached2 * ACached2[i] + acc[i];
          ${i===3?"":"acc[i] = BCached3 * ACached3[i] + acc[i];"}
        }`:`
        for (var i = 0; i < rowPerThread; i = i + 1) {
          let ACached = mm_Asub[tileRow + i][k];
          acc[i] = BCached0 * ACached.x + acc[i];
          acc[i] = BCached1 * ACached.y + acc[i];
          acc[i] = BCached2 * ACached.z + acc[i];
          ${i===3?"":"acc[i] = BCached3 * ACached.w + acc[i];"}
        }`,ml=(t,i,n="f32",u,l=!1,c=32,h=!1,y=32)=>{let b=i[1]*t[1],$=i[0]*t[0],k=l?b:c,I=l?c:b,O=k/i[0],M=c/i[1];if(!((l&&O===4&&t[1]===4||!l&&(O===3||O===4))&&k%i[0]===0&&c%i[1]===0&&t[0]===4))throw new Error(`If transposeA ${l} is true, innerElementSize ${O} and workPerThread[1] ${t[1]} must be 4.
      Otherwise, innerElementSize ${O} must be 3 or 4.
  tileAWidth ${k} must be divisible by workgroupSize[0]${i[0]}. tileInner ${c} must be divisible by workgroupSize[1] ${i[1]}. colPerThread ${t[0]} must be 4.`);return`
var<workgroup> mm_Asub: array<array<vec${O}<${n}>, ${k/O}>, ${I}>;
var<workgroup> mm_Bsub: array<array<vec4<${n}>, ${$/t[0]}>, ${c}>;

const rowPerThread = ${t[1]};
const colPerThread = ${t[0]};
const innerElementSize = ${O};
const tileInner = ${c};

@compute @workgroup_size(${i[0]}, ${i[1]}, ${i[2]})
fn main(@builtin(local_invocation_id) localId : vec3<u32>,
        @builtin(global_invocation_id) globalId : vec3<u32>,
        @builtin(workgroup_id) workgroupId : vec3<u32>) {
  let localRow = i32(localId.y);
  let tileRow = localRow * rowPerThread;
  let tileCol = i32(localId.x);

  let globalRow =i32(globalId.y) * rowPerThread;
  let globalCol = i32(globalId.x);
  let batch = ${h?"0":"i32(globalId.z)"};
  ${u?`let batchIndices = ${u.offsetToIndices("u32(batch)")};`:""}
  let globalRowStart = i32(workgroupId.y) * ${b};

  let num_tiles = ${h?`${Math.ceil(y/c)}`:"(uniforms.dim_inner - 1) / tileInner + 1"};
  var kStart = ${h?`i32(globalId.z) * ${y}`:"0"};

  var acc: array<vec4<${n}>, rowPerThread>;

  // Loop over shared dimension.
  let tileRowB = localRow * ${M};
  for (var t = 0; t < num_tiles; t = t + 1) {
      // Load one tile of A into local memory.
      for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
          let inputRow = tileRow + innerRow;
          let inputCol = tileCol;
          ${Yp(l,u)}
      }

      // Load one tile of B into local memory.
      for (var innerRow = 0; innerRow < ${M}; innerRow = innerRow + 1) {
          let inputRow = tileRowB + innerRow;
          let inputCol = tileCol;
          mm_Bsub[inputRow][inputCol] = mm_readB(batch, kStart + inputRow, globalCol${u?", batchIndices":""});
      }
      kStart = kStart + tileInner;
      workgroupBarrier();

      // Compute acc values for a single thread.
      for (var k = 0; k < tileInner / innerElementSize; k = k + 1) {
          let BCached0 = mm_Bsub[k * innerElementSize][tileCol];
          let BCached1 = mm_Bsub[k * innerElementSize + 1][tileCol];
          let BCached2 = mm_Bsub[k * innerElementSize + 2][tileCol];
          ${O===3?"":"let BCached3 = mm_Bsub[k * innerElementSize + 3][tileCol];"}

          ${Jp(l,O)}
      }

      workgroupBarrier();
  }

  for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
      mm_write(batch, globalRow + innerRow, globalCol, acc[innerRow]);
  }
}`},gl=(t,i)=>t?`
            mm_Asub[inputRow][inputCol] = mm_readA(batch,
              kStart + inputRow,
              globalRowStart + inputCol${i?", batchIndices":""});
            `:`
            mm_Asub[inputRow][inputCol] = mm_readA(batch,
              globalRowStart + inputRow,
              kStart + inputCol${i?", batchIndices":""});
            `,ec=t=>t?"let ACached = mm_Asub[k][tileRow + innerRow];":"let ACached = mm_Asub[tileRow + innerRow][k];",yl=(t,i,n="f32",u,l=!1,c=32,h=!1,y=32,b=!1)=>{let $=t[1]*i[1],k=t[0]*i[0],I=l?$:c,O=l?c:$;if(!(O%i[1]===0&&I%i[0]===0&&c%i[1]===0))throw new Error(`tileAHight ${O} must be divisible by workgroupSize[1]${i[1]}, tileAWidth ${I} must be divisible by workgroupSize[0]${i[0]}, tileInner ${c} must be divisible by workgroupSize[1]${i[1]}`);let M=O/i[1],D=I/i[0],L=c/i[1],Z=b?`
    let localRow = i32(localId.y);
    let localCol = i32(localId.x);
    let globalRowStart = i32(workgroupId.y) * ${$};
    let globalColStart = i32(workgroupId.x) * ${k};

    // Loop over shared dimension.
    for (var t = 0; t < num_tiles; t = t + 1) {
      // Load one tile of A into local memory.
      for (var inputRow = localRow; inputRow < ${O}; inputRow = inputRow + ${i[1]}) {
        for (var inputCol = localCol; inputCol < ${I}; inputCol = inputCol + ${i[0]}) {
          ${gl(l,u)}
        }
      }
      // Load one tile of B into local memory.
      for (var inputRow = localRow; inputRow < ${c}; inputRow = inputRow + ${i[1]}) {
            for (var inputCol = localCol; inputCol < ${k}; inputCol = inputCol + ${i[0]}) {
          mm_Bsub[inputRow][inputCol] = mm_readB(batch,
            kStart + inputRow,
            globalColStart + inputCol${u?", batchIndices":""});
        }
      }
      kStart = kStart + tileInner;
      workgroupBarrier();

      // Compute acc values for a single thread.
      var BCached : array<${n}, colPerThread>;
      for (var k = 0; k < tileInner; k = k + 1) {
        for (var inner = 0; inner < colPerThread; inner = inner + 1) {
          BCached[inner] = mm_Bsub[k][localCol + inner * ${i[0]}];
        }
        for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
          let ACached = ${l?`mm_Asub[k][localRow + innerRow * ${i[1]}];`:`mm_Asub[localRow + innerRow * ${i[1]}][k];`}
          for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
            acc[innerRow][innerCol] = acc[innerRow][innerCol] +
                ACached * BCached[innerCol];
          }
        }
      }
      workgroupBarrier();
    }
    for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
      let gRow = globalRowStart + localRow + innerRow * ${i[1]};
      for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
        let gCol = globalColStart + localCol + innerCol * ${i[0]};
        mm_write(batch, gRow, gCol, acc[innerRow][innerCol]);
      }
    }
    `:`
let tileRow = i32(localId.y) * rowPerThread;
let tileCol = i32(localId.x) * colPerThread;

let globalRow = i32(globalId.y) * rowPerThread;
let globalCol = i32(globalId.x) * colPerThread;
let globalRowStart = i32(workgroupId.y) * ${$};

let tileRowA = i32(localId.y) * ${M};
let tileColA = i32(localId.x) * ${D};
let tileRowB = i32(localId.y) * ${L};
// Loop over shared dimension.
for (var t = 0; t < num_tiles; t = t + 1) {
  // Load one tile of A into local memory.
  for (var innerRow = 0; innerRow < ${M}; innerRow = innerRow + 1) {
    for (var innerCol = 0; innerCol < ${D}; innerCol = innerCol + 1) {
      let inputRow = tileRowA + innerRow;
      let inputCol = tileColA + innerCol;
      ${gl(l,u)}
    }
  }

  // Load one tile of B into local memory.
  for (var innerRow = 0; innerRow < ${L}; innerRow = innerRow + 1) {
    for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
      let inputRow = tileRowB + innerRow;
      let inputCol = tileCol + innerCol;
      mm_Bsub[inputRow][inputCol] = mm_readB(batch,
        kStart + inputRow,
        globalCol + innerCol${u?", batchIndices":""});
    }
  }
  kStart = kStart + tileInner;
  workgroupBarrier();

  // Compute acc values for a single thread.
  var BCached : array<${n}, colPerThread>;
  for (var k = 0; k < tileInner; k = k + 1) {
    for (var inner = 0; inner < colPerThread; inner = inner + 1) {
      BCached[inner] = mm_Bsub[k][tileCol + inner];
    }

    for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
      ${ec(l)}
      for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
        acc[innerRow][innerCol] = acc[innerRow][innerCol] + ACached * BCached[innerCol];
      }
    }
  }

  workgroupBarrier();
}

for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
  for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
    mm_write(batch, globalRow + innerRow, globalCol + innerCol,
        acc[innerRow][innerCol]);
  }
}
`;return`
  var<workgroup> mm_Asub : array<array<${n}, ${I}>, ${O}>;
  var<workgroup> mm_Bsub : array<array<${n}, ${k}>, ${c}>;
  const rowPerThread = ${t[1]};
  const colPerThread = ${t[0]};
  const tileInner = ${c};

@compute @workgroup_size(${i[0]}, ${i[1]}, ${i[2]})
fn main(@builtin(local_invocation_id) localId : vec3<u32>,
        @builtin(global_invocation_id) globalId : vec3<u32>,
        @builtin(workgroup_id) workgroupId : vec3<u32>) {
    let batch = ${h?"0":"i32(globalId.z)"};
    ${u?`let batchIndices = ${u.offsetToIndices("u32(batch)")};`:""}
    let num_tiles = ${h?`${Math.ceil(y/c)}`:"(uniforms.dim_inner - 1) / tileInner + 1"};
    var kStart = ${h?`i32(globalId.z) * ${y}`:"0"};

    var acc : array<array<${n}, colPerThread>, rowPerThread>;
    ${Z}
  }
`},tc=(t,i,n,u,l=!1)=>{let[c,h,y,b]=u,$=ue(u[0].type.tensor);return`
    fn mm_readA(batch: i32, row: i32, colIn: i32, batchIndices: ${c.type.indices}) -> ${nr(t,$)} {
      var value = ${nr(t,$)}(0.0);
      let col = colIn * ${t};
      if(row < uniforms.dim_a_outer && col < uniforms.dim_inner)
      {
        var aIndices: ${h.type.indices};
        ${co("aIndices",h,h.rank-2,c.rank,"batchIndices")}
        ${h.indicesSet("aIndices",h.rank-2,"u32(row)")}
        ${h.indicesSet("aIndices",h.rank-1,"u32(colIn)")}
        value = ${h.getByIndices("aIndices")};
      }
      return value;
    }

    fn mm_readB(batch: i32, row: i32, colIn: i32, batchIndices: ${c.type.indices}) -> ${nr(t,$)} {
      var value = ${nr(t,$)}(0.0);
      let col = colIn * ${t};
      if(row < uniforms.dim_inner && col < uniforms.dim_b_outer)
      {
        var bIndices: ${y.type.indices};
        ${co("bIndices",y,y.rank-2,c.rank,"batchIndices")}
        ${y.indicesSet("bIndices",y.rank-2,"u32(row)")}
        ${y.indicesSet("bIndices",y.rank-1,"u32(colIn)")}
        value = ${y.getByIndices("bIndices")};
      }
      return value;
    }

    fn mm_write(batch: i32, row: i32, colIn: i32, valueIn: ${nr(t,$)}) {
      let col = colIn * ${t};
      if (row < uniforms.dim_a_outer && col < uniforms.dim_b_outer) {
        var value = valueIn;
        let coords = vec3<i32>(batch, row, colIn);
        ${i?`value = value + ${l?"bias[colIn]":`${nr(t,$)}(bias[row])`};`:""}
        ${n}
        ${b.setByIndices("vec3<u32>(coords)","value")}
      }
    }
    `},Cu=(t,i,n,u,l=!1,c)=>{let h=t[0].dims,y=t[1].dims,b=h.slice(0,-2),$=y.slice(0,-2),k=u?u.slice(0,-2):n.slice(0,-2),I=he.size(k),O=h[h.length-2],M=h[h.length-1],D=y[y.length-1],L=M%4===0&&D%4===0,Z=O<=8?[4,1,1]:[4,4,1],W=[8,8,1],V=[Math.ceil(D/W[0]/Z[0]),Math.ceil(O/W[1]/Z[1]),Math.ceil(I/W[2]/Z[2])],J=L?4:1,Y=[...b,O,M/J],se=Y.length,de=[...$,M,D/J],fe=de.length,we=[I,O,D/J],xe=[{type:6,data:O},{type:6,data:D},{type:6,data:M}];At(i,xe),xe.push(...ie(k,Y,de));let De=["rank","rank"],at=t.length>2;at&&(xe.push(...ie(t[2].dims)),De.push("rank")),xe.push(...ie(we));let et=tt=>{let xt=k.length,zt=dt("batchDims",t[0].dataType,xt,1),rt=ue(t[0].dataType),ot=oe("a",t[0].dataType,se,J),ur=oe("b",t[1].dataType,fe,J),Ne=ke("result",t[0].dataType,we.length,J),Ot=[ot,ur];if(at){let Vt=l?J:1;Ot.push(oe("bias",t[2].dataType,t[2].dims.length,Vt))}let Ae=[{name:"dim_a_outer",type:"i32"},{name:"dim_b_outer",type:"i32"},{name:"dim_inner",type:"i32"}];Ar(i,Ae);let Ge=ue(Ne.type.tensor),Ze=Et(i,Ne.type.value,Ge),Pe=tc(J,at,Ze,[zt,ot,ur,Ne],l);return`
  ${tt.registerUniforms(Ae).registerInternalVariables(zt).declareVariables(...Ot,Ne)}
  ${Pe}
  ${L?ml(Z,W,rt,zt):yl(Z,W,rt,zt)}
                   `};return{name:"MatMul",shaderCache:{hint:`${Z};${i.activation};${L};${l}`,inputDependencies:De},getRunData:()=>({outputs:[{dims:c?c(n):n,dataType:t[0].dataType}],dispatchGroup:{x:V[0],y:V[1],z:V[2]},programUniforms:xe}),getShaderSource:et}}}),rc,ic,Ev=m(()=>{it(),Mr(),Ke(),ga(),cl(),kv(),_l(),rc=(t,i,n,u,l=!1,c,h=4,y=4,b=4,$="f32")=>{let k=xe=>{switch(xe){case 1:return"resData = x[xIndex];";case 3:return`resData = vec3<${$}>(x[xIndex], x[xIndex + 1], x[xIndex + 2]);`;case 4:return"resData = x[xIndex / 4];";default:throw new Error(`innerElementSize ${xe} is not supported.`)}},I=xe=>{switch(xe){case 1:return"return w[row * i32(uniforms.w_shape[3]) + colIn];";case 4:return"return w[row * i32(uniforms.w_shape[3]) / 4 + colIn];";default:throw new Error(`innerElementSize ${xe} is not supported.`)}},O=t?`
    let coord = vec4<i32>(batch, xRow, xCol, xCh);
    `:`
    let coord = vec4<i32>(batch, xCh, xRow, xCol);
    `,M=t?`
    let coords = vec4<i32>(
      batch,
      row / outWidth,
      row % outWidth,
      col);
    `:`
    let coords = vec4<i32>(
      batch,
      row,
      col / outWidth,
      col % outWidth);
    `,D=t?"i32(uniforms.x_shape[1])":"i32(uniforms.x_shape[2])",L=t?"i32(uniforms.x_shape[2])":"i32(uniforms.x_shape[3])",Z=t?"row":"col",W=t?"col":"row",V=`
    let inChannels = i32(uniforms.w_shape[2]);
    let outWidth = ${t?"i32(uniforms.result_shape[2])":"i32(uniforms.result_shape[3])"};
    let outRow = ${Z} / outWidth;
    let outCol = ${Z} % outWidth;

    let WRow = ${W} / (i32(uniforms.w_shape[1]) * inChannels);
    let WCol = ${W} / inChannels % i32(uniforms.w_shape[1]);
    let xRow = outRow * uniforms.stride[0] + uniforms.dilation[0] * WRow - uniforms.pad[0];
    let xCol = outCol * uniforms.stride[1] + uniforms.dilation[1] * WCol - uniforms.pad[1];
    let xCh = ${W} % inChannels;
    var resData = ${nr(h,$)}(0.0);
    // The bounds checking is always needed since we use it to pad zero for
    // the 'same' padding type.
    if (xRow >= 0 && xRow < ${D} && xCol >= 0 && xCol < ${L}) {
      ${O}
      let xIndex = getIndexFromCoords4D(coord, vec4<i32>(uniforms.x_shape));
      ${k(h)}
    }
    return resData;`,J=t?i&&u?`
    let col = colIn * ${h};
    ${V}`:`
    let col = colIn * ${h};
    if (row < uniforms.dim_a_outer && col < uniforms.dim_inner) {
      ${V}
    }
    return ${nr(h,$)}(0.0);`:u&&n?`
    let col = colIn * ${h};
    ${V}`:`
    let col = colIn * ${h};
    if (row < uniforms.dim_inner && col < uniforms.dim_b_outer) {
      ${V}
    }
    return ${nr(h,$)}(0.0);`,Y=t?u&&n?I(y):`
    let col = colIn * ${y};
    if (row < uniforms.dim_inner && col < uniforms.dim_b_outer) {
      ${I(y)}
    }
    return ${nr(y,$)}(0.0);`:`
    let col = colIn * ${y};
    if (row < uniforms.dim_inner && col < uniforms.dim_a_outer) {
      ${I(y)}
    }
    return ${nr(y,$)}(0.0);`,se=nr(b,$),de=nr(t?h:y,$),fe=nr(t?y:h,$),we=Et(c,se,$);return`
    fn mm_readA(batch: i32, row : i32, colIn : i32) -> ${de} {
      ${t?J:Y}
    }

    fn mm_readB(batch: i32, row : i32, colIn : i32) -> ${fe} {
      ${t?Y:J}
    }

    fn mm_write(batch: i32, row : i32, colIn : i32, valueIn : ${se}) {
      let col = colIn * ${b};
      if (row < uniforms.dim_a_outer && col < uniforms.dim_b_outer)
      {
      var value = valueIn;
      let outWidth = ${t?"i32(uniforms.result_shape[2])":"i32(uniforms.result_shape[3])"};
      ${M}
      ${Qp(l)}
      ${we}
      setOutputAtCoords(coords[0], coords[1], coords[2], coords[3], value);
      }
    }`},ic=(t,i,n,u,l,c,h,y,b)=>{let $=i.format==="NHWC",k=$?t[0].dims[3]:t[0].dims[1],I=n[0],O=$?n[2]:n[3],M=$?n[1]:n[2],D=$?n[3]:n[1],L=$&&(k%4===0||k%3===0)&&D%4===0,Z=$?D:O*M,W=$?O*M:D,V=[8,8,1],J=u<=8?[4,1,1]:[4,4,1],Y=[Math.ceil(Z/V[0]/J[0]),Math.ceil(W/V[1]/J[1]),Math.ceil(I/V[2]/J[2])];mt("verbose",()=>`[conv2d_mm_webgpu] dispatch = ${Y}`);let se=L?$&&k%4!==0?3:4:1,de=V[1]*J[1],fe=V[0]*J[0],we=Math.max(V[0]*se,V[1]),xe=u%de===0,De=l%fe===0,at=c%we===0,et=L?[se,4,4]:[1,1,1],tt=[{type:6,data:u},{type:6,data:l},{type:6,data:c},{type:6,data:[i.pads[0],i.pads[1]]},{type:6,data:i.strides},{type:6,data:i.dilations}];At(i,tt),tt.push(...ie(t[0].dims,t[1].dims));let xt=["rank","rank"];h&&(tt.push(...ie(t[2].dims)),xt.push("rank")),tt.push(...ie(n));let zt=rt=>{let ot=[{name:"dim_a_outer",type:"i32"},{name:"dim_b_outer",type:"i32"},{name:"dim_inner",type:"i32"},{name:"pad",type:"i32",length:2},{name:"stride",type:"i32",length:2},{name:"dilation",type:"i32",length:2}];Ar(i,ot);let ur=L?4:1,Ne=ue(t[0].dataType),Ot=`
      fn setOutputAtIndex(flatIndex : i32, value : ${L?`vec4<${Ne}>`:Ne}) {
        result[flatIndex] = ${L?`vec4<${Ne}>`:Ne}(value);
      }
      fn setOutputAtCoords(d0 : i32, d1 : i32, d2 : i32, d3 : i32, value : ${L?`vec4<${Ne}>`:Ne}) {
        let flatIndex = getOutputIndexFromCoords(vec4<i32>(d0, d1, d2, d3));
        setOutputAtIndex(flatIndex ${L?"/ 4":""}, value);
      }`,Ae=oe("x",t[0].dataType,t[0].dims.length,se===3?1:se),Ge=oe("w",t[1].dataType,t[1].dims.length,ur),Ze=[Ae,Ge],Pe=ke("result",t[0].dataType,n.length,ur);if(h){let Vt=oe("bias",t[2].dataType,t[2].dims.length,ur);Ze.push(Vt),Ot+=`
        fn getBiasByOutputCoords(coords : vec4<i32>) -> ${L?`vec4<${Ne}>`:Ne} {
          return bias[coords.${$?"w":"y"}${L?"/ 4":""}];
        }`}return`
        ${Xp("uniforms.result_strides")}
        //struct Uniforms { xShape : vec4<i32>, wShape : vec4<i32>, outShape : vec4<i32>,
        //  outShapeStrides: vec3<i32>, filterDims : vec2<i32>, pad : vec2<i32>, stride : vec2<i32>,
        //  dilation : vec2<i32>, dimAOuter : i32, dimBOuter : i32, dimInner : i32 };
        ${rt.registerUniforms(ot).declareVariables(...Ze,Pe)}
        ${Ot}
        ${rc($,xe,De,at,h,i,et[0],et[1],et[2],Ne)}
        ${L?ml(J,V,Ne,void 0,!$,we):yl(J,V,Ne,void 0,!$,we,!1,void 0,y)}`};return{name:"Conv2DMatMul",shaderCache:{hint:`${i.cacheKey};${se};${L};${xe};${De};${at};${de};${fe};${we}`,inputDependencies:xt},getRunData:()=>({outputs:[{dims:b?b(n):n,dataType:t[0].dataType}],dispatchGroup:{x:Y[0],y:Y[1],z:Y[2]},programUniforms:tt}),getShaderSource:zt}}}),ac,wl,ho,nc,bl,sc,oc,uc,Iv=m(()=>{it(),Mr(),Xe(),Ke(),ga(),cl(),ac=t=>{let i=1;for(let n=0;n<t.length;n++)i*=t[n];return i},wl=t=>typeof t=="number"?[t,t,t]:t,ho=(t,i)=>i<=1?t:t+(t-1)*(i-1),nc=(t,i,n,u=1)=>{let l=ho(i,u);return Math.floor((t[0]*(n-1)-n+l)/2)},bl=(t,i,n,u,l)=>{l==null&&(l=nc(t,i[0],u[0]));let c=[0,0,0,n];for(let h=0;h<3;h++)t[h]+2*l>=i[h]&&(c[h]=Math.trunc((t[h]-i[h]+2*l)/u[h]+1));return c},sc=(t,i,n,u,l,c,h,y,b,$)=>{let k,I,O,M;if(t==="VALID"&&(t=0),typeof t=="number"){k={top:t,bottom:t,left:t,right:t,front:t,back:t};let D=bl([i,n,u,1],[y,b,$],1,[l,c,h],t);I=D[0],O=D[1],M=D[2]}else if(Array.isArray(t)){if(!t.every((L,Z,W)=>L===W[0]))throw Error(`Unsupported padding parameter: ${t}`);k={top:t[0],bottom:t[1],left:t[2],right:t[3],front:t[4],back:t[5]};let D=bl([i,n,u,1],[y,b,$],1,[l,c,h],t[0]);I=D[0],O=D[1],M=D[2]}else if(t==="SAME_UPPER"){I=Math.ceil(i/l),O=Math.ceil(n/c),M=Math.ceil(u/h);let D=(I-1)*l+y-i,L=(O-1)*c+b-n,Z=(M-1)*h+$-u,W=Math.floor(D/2),V=D-W,J=Math.floor(L/2),Y=L-J,se=Math.floor(Z/2),de=Z-se;k={top:J,bottom:Y,left:se,right:de,front:W,back:V}}else throw Error(`Unknown padding parameter: ${t}`);return{padInfo:k,outDepth:I,outHeight:O,outWidth:M}},oc=(t,i,n,u,l,c=!1,h="channelsLast")=>{let y,b,$,k,I;if(h==="channelsLast")[y,b,$,k,I]=t;else if(h==="channelsFirst")[y,I,b,$,k]=t;else throw new Error(`Unknown dataFormat ${h}`);let[O,,M,D,L]=i,[Z,W,V]=wl(n),[J,Y,se]=wl(u),de=ho(M,J),fe=ho(D,Y),we=ho(L,se),{padInfo:xe,outDepth:De,outHeight:at,outWidth:et}=sc(l,b,$,k,Z,W,V,de,fe,we),tt=c?O*I:O,xt=[0,0,0,0,0];return h==="channelsFirst"?xt=[y,tt,De,at,et]:h==="channelsLast"&&(xt=[y,De,at,et,tt]),{batchSize:y,dataFormat:h,inDepth:b,inHeight:$,inWidth:k,inChannels:I,outDepth:De,outHeight:at,outWidth:et,outChannels:tt,padInfo:xe,strideDepth:Z,strideHeight:W,strideWidth:V,filterDepth:M,filterHeight:D,filterWidth:L,effectiveFilterDepth:de,effectiveFilterHeight:fe,effectiveFilterWidth:we,dilationDepth:J,dilationHeight:Y,dilationWidth:se,inShape:t,outShape:xt,filterShape:i}},uc=(t,i,n,u,l,c)=>{let h=c==="channelsLast";h?t[0].dims[3]:t[0].dims[1];let y=[64,1,1],b={x:n.map((Z,W)=>W)},$=[Math.ceil(ac(b.x.map(Z=>n[Z]))/y[0]),1,1];mt("verbose",()=>`[conv3d_naive_webgpu] dispatch = ${$}`);let k=1,I=he.size(n),O=[{type:12,data:I},{type:12,data:u},{type:12,data:l},{type:12,data:i.strides},{type:12,data:i.dilations}];At(i,O),O.push(...ie(t[0].dims,t[1].dims));let M=["rank","rank"],D=t.length===3;D&&(O.push(...ie(t[2].dims)),M.push("rank")),O.push(...ie(n));let L=Z=>{let W=[{name:"output_size",type:"u32"},{name:"filter_dims",type:"u32",length:u.length},{name:"pads",type:"u32",length:l.length},{name:"strides",type:"u32",length:i.strides.length},{name:"dilations",type:"u32",length:i.dilations.length}];Ar(i,W);let V=1,J=ue(t[0].dataType),Y=oe("x",t[0].dataType,t[0].dims.length,k),se=oe("W",t[1].dataType,t[1].dims.length,V),de=[Y,se],fe=ke("result",t[0].dataType,n.length,V),we="";if(D){let at=oe("bias",t[2].dataType,t[2].dims.length,V);de.push(at),we+=`
        fn getBiasByOutputCoords(coords : array<u32, 5>) -> ${J} {
          return bias[${h?ce("coords",4,5):ce("coords",1,5)}];
        }`}let xe=nr(k,J),De=Et(i,xe,J);return`
            ${we}
            fn getX(d0 : u32, d1 : u32, d2 : u32, d3 : u32, d4 : u32) -> f32 {
              let aIndices = array<u32, 5>(d0, d1, d2, d3, d4);
              return ${Y.getByIndices("aIndices")};
            }
            fn getW(d0 : u32, d1 : u32, d2 : u32, d3 : u32, d4 : u32) -> f32 {
              let aIndices = array<u32, 5>(d0, d1, d2, d3, d4);
              return ${se.getByIndices("aIndices")};
            }
          ${Z.registerUniforms(W).declareVariables(...de,fe)}
          ${Z.mainStart()}
          ${Z.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
              let coords = ${fe.offsetToIndices("global_idx")};
              let batch = ${ce("coords",0,Y.rank)};
              let d2 = ${h?ce("coords",Y.rank-1,Y.rank):ce("coords",1,Y.rank)};
              let xFRCCorner = vec3<u32>(${h?ce("coords",1,Y.rank):ce("coords",2,Y.rank)},
              ${h?ce("coords",2,Y.rank):ce("coords",3,Y.rank)},
              ${h?ce("coords",3,Y.rank):ce("coords",4,Y.rank)}) * uniforms.strides - uniforms.pads;
              let xFCorner = xFRCCorner.x;
              let xRCorner = xFRCCorner.y;
              let xCCorner = xFRCCorner.z;
              let xShapeY = ${h?ce("uniforms.x_shape",1,Y.rank):ce("uniforms.x_shape",2,Y.rank)};
              let xShapeZ = ${h?ce("uniforms.x_shape",2,Y.rank):ce("uniforms.x_shape",3,Y.rank)};
              let xShapeW = ${h?ce("uniforms.x_shape",3,Y.rank):ce("uniforms.x_shape",4,Y.rank)};
              let xShapeU = ${h?ce("uniforms.x_shape",4,Y.rank):ce("uniforms.x_shape",1,Y.rank)};
              let inputDepthNearestVec4 = (xShapeU / 4) * 4;
              let inputDepthVec4Remainder = xShapeU % 4;

              var value = 0.0;
              for (var wF = 0u; wF < uniforms.filter_dims[0]; wF++) {
                let xF = xFCorner + wF * uniforms.dilations[0];
                if (xF < 0 || xF >= xShapeY) {
                  continue;
                }

                for (var wR = 0u; wR < uniforms.filter_dims[1]; wR++) {
                  let xR = xRCorner + wR * uniforms.dilations[1];
                  if (xR < 0 || xR >= xShapeZ) {
                    continue;
                  }

                  for (var wC = 0u; wC < uniforms.filter_dims[2]; wC++) {
                    let xC = xCCorner + wC * uniforms.dilations[2];
                    if (xC < 0 || xC >= xShapeW) {
                      continue;
                    }

                    for (var d1 = 0u; d1 < inputDepthNearestVec4; d1 += 4) {
                      ${h?`let xValues = vec4<f32>(
                               getX(batch, xF, xR, xC, d1),
                               getX(batch, xF, xR, xC, d1 + 1),
                               getX(batch, xF, xR, xC, d1 + 2),
                               getX(batch, xF, xR, xC, d1 + 3));
                            `:`let xValues = vec4<f32>(
                               getX(batch, d1, xF, xR, xC),
                               getX(batch, d1 + 1, xF, xR, xC),
                               getX(batch, d1 + 2, xF, xR, xC),
                               getX(batch, d1 + 3, xF, xR, xC));
                            `}
                            let wValues = vec4<f32>(
                              getW(d2, d1, wF, wR, wC),
                              getW(d2, d1 + 1, wF, wR, wC),
                              getW(d2, d1 + 2, wF, wR, wC),
                              getW(d2, d1 + 3, wF, wR, wC));
                      value += dot(xValues, wValues);
                    }
                    if (inputDepthVec4Remainder == 1) {
                        ${h?`value += getX(batch, xF, xR, xC, inputDepthNearestVec4)
                          * getW(d2, inputDepthNearestVec4, wF, wR, wC);`:`value += getX(batch, inputDepthNearestVec4, xF, xR, xC)
                          * getW(d2, inputDepthNearestVec4, wF, wR, wC);`}
                    } else if (inputDepthVec4Remainder == 2) {
                      ${h?`let xValues = vec2<f32>(
                        getX(batch, xF, xR, xC, inputDepthNearestVec4),
                        getX(batch, xF, xR, xC, inputDepthNearestVec4 + 1));
                      `:`let xValues = vec2<f32>(
                        getX(batch, inputDepthNearestVec4, xF, xR, xC),
                        getX(batch, inputDepthNearestVec4 + 1, xF, xR, xC));
                    `}
                    let wValues = vec2<f32>(
                      getW(d2, inputDepthNearestVec4, wF, wR, wC),
                      getW(d2, inputDepthNearestVec4 + 1, wF, wR, wC));
                      value += dot(xValues, wValues);
                    } else if (inputDepthVec4Remainder == 3) {
                      ${h?`let xValues = vec3<f32>(
                        getX(batch, xF, xR, xC, inputDepthNearestVec4),
                        getX(batch, xF, xR, xC, inputDepthNearestVec4 + 1),
                        getX(batch, xF, xR, xC, inputDepthNearestVec4 + 2));
                      `:`let xValues = vec3<f32>(
                        getX(batch, inputDepthNearestVec4, xF, xR, xC),
                        getX(batch, inputDepthNearestVec4 + 1, xF, xR, xC),
                        getX(batch, inputDepthNearestVec4 + 2, xF, xR, xC));
                    `}
                    let wValues = vec3<f32>(
                      getW(d2, inputDepthNearestVec4, wF, wR, wC),
                      getW(d2, inputDepthNearestVec4 + 1, wF, wR, wC),
                      getW(d2, inputDepthNearestVec4 + 2, wF, wR, wC));
                      value += dot(xValues, wValues);
                    }
                  }
                }
              }
              ${D?"value = value + getBiasByOutputCoords(coords)":""};
              ${De}
              result[global_idx] = f32(value);
          }`};return{name:"Conv3DNaive",shaderCache:{hint:`${i.cacheKey};${h};${k};${D}`,inputDependencies:M},getRunData:()=>({outputs:[{dims:n,dataType:t[0].dataType}],dispatchGroup:{x:$[0],y:$[1],z:$[2]},programUniforms:O}),getShaderSource:L}}}),lc,dc,zv=m(()=>{it(),Xe(),Ke(),ga(),lc=(t,i,n,u)=>{let l=t.length>2,c=l?"value += b[output_channel];":"",h=t[0].dims,y=t[1].dims,b=i.format==="NHWC",$=b?n[3]:n[1],k=$/i.group,I=b&&k>=4?le($):1,O=he.size(n)/I,M=[{type:12,data:O},{type:12,data:i.dilations},{type:12,data:[i.strides[0],i.strides[1]]},{type:12,data:[i.pads[0],i.pads[1]]},{type:12,data:k}];At(i,M),M.push(...ie(h,[y[0],y[1],y[2],y[3]/I]));let D=l?["rank","rank","rank"]:["rank","rank"];M.push(...ie([n[0],n[1],n[2],n[3]/I]));let L=Z=>{let W=ke("output",t[0].dataType,n.length,I),V=ue(W.type.tensor),J=Et(i,W.type.value,V),Y=oe("x",t[0].dataType,h.length),se=oe("w",t[1].dataType,y.length,I),de=[Y,se];l&&de.push(oe("b",t[2].dataType,t[2].dims,I));let fe=[{name:"output_size",type:"u32"},{name:"dilations",type:"u32",length:i.dilations.length},{name:"strides",type:"u32",length:2},{name:"pads",type:"u32",length:2},{name:"output_channels_per_group",type:"u32"}];Ar(i,fe);let we=b?`
      for (var wHeight: u32 = 0u; wHeight < uniforms.w_shape[0]; wHeight++) {
        let xHeight = xRCCorner.x + wHeight * uniforms.dilations[0];

        if (xHeight < 0u || xHeight >= uniforms.x_shape[1]) {
          continue;
        }

        for (var wWidth: u32 = 0u; wWidth < uniforms.w_shape[1]; wWidth++) {
          let xWidth = xRCCorner.y + wWidth * uniforms.dilations[1];
          if (xWidth < 0u || xWidth >= uniforms.x_shape[2]) {
            continue;
          }

          for (var wInChannel: u32 = 0u; wInChannel < uniforms.w_shape[2]; wInChannel++) {
            let input_channel = in_channel_offset + wInChannel;
            let xVal = ${Y.get("batch","xHeight","xWidth","input_channel")};
            let wVal = ${se.get("wHeight","wWidth","wInChannel","output_channel")};
            value += xVal * wVal;
          }
        }
      }
      `:`
      for (var wInChannel: u32 = 0u; wInChannel < uniforms.w_shape[1]; wInChannel++) {
        let input_channel = in_channel_offset + wInChannel;
        for (var wHeight: u32 = 0u; wHeight < uniforms.w_shape[2]; wHeight++) {
          let xHeight = xRCCorner.x + wHeight * uniforms.dilations[0];

          if (xHeight < 0u || xHeight >= uniforms.x_shape[2]) {
            continue;
          }

          for (var wWidth: u32 = 0u; wWidth < uniforms.w_shape[3]; wWidth++) {
            let xWidth = xRCCorner.y + wWidth * uniforms.dilations[1];
            if (xWidth < 0u || xWidth >= uniforms.x_shape[3]) {
              continue;
            }

            let xVal = ${Y.get("batch","input_channel","xHeight","xWidth")};
            let wVal = ${se.get("output_channel","wInChannel","wHeight","wWidth")};
            value += xVal * wVal;
          }
        }
      }
      `;return`
  ${Z.registerUniforms(fe).declareVariables(...de,W)}

  ${Z.mainStart()}
    ${Z.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let outputIndices = ${W.offsetToIndices("global_idx")};
    let batch: u32 = outputIndices[0];
    let output_channel: u32 = outputIndices[${b?3:1}];
    let xRCCorner: vec2<u32> = vec2<u32>(outputIndices[${b?1:2}], outputIndices[${b?2:3}]) * uniforms.strides - uniforms.pads;
    let group_id: u32 = output_channel * ${I} / uniforms.output_channels_per_group;
    var in_channel_offset = group_id * uniforms.w_shape[${b?2:1}];

    var value: ${W.type.value} = ${W.type.value}(0);
    ${we}
    ${c}
    ${J}
    ${W.setByOffset("global_idx","value")}
  }`};return{name:"GroupedConv",shaderCache:{hint:`${i.cacheKey}_${I}`,inputDependencies:D},getRunData:()=>({outputs:[{dims:u?u(n):n,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(O/64)},programUniforms:M}),getShaderSource:L}},dc=(t,i,n,u)=>{let l=t.length>2,c=le(n[3]),h=le(n[2]),y=he.size(n)/c/h,b=[t[0].dims[0],t[0].dims[1],t[0].dims[2],t[0].dims[3]/c],$=[t[1].dims[0],t[1].dims[1],t[1].dims[2],t[1].dims[3]/c],k=[n[0],n[1],n[2],n[3]/c],I=[{type:12,data:y},{type:6,data:[i.strides[0],i.strides[1]]},{type:6,data:[i.pads[0],i.pads[1]]}];At(i,I),I.push(...ie(b,$,k));let O=(h-1)*i.strides[1]+$[1],M=D=>{let L=ke("output",t[0].dataType,k.length,c),Z=ue(L.type.tensor),W=Et(i,L.type.value,Z),V=oe("x",t[0].dataType,b.length,c),J=oe("w",t[1].dataType,$.length,c),Y=[V,J];l&&Y.push(oe("b",t[2].dataType,t[2].dims,c));let se=l?"value += b[output_channel];":"",de=[{name:"output_size",type:"u32"},{name:"strides",type:"i32",length:2},{name:"pads",type:"i32",length:2}];return Ar(i,de),`
  ${D.registerUniforms(de).declareVariables(...Y,L)}
  ${D.mainStart()}
    ${D.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let width0 = uniforms.output_shape[3];
    let output_channel = global_idx % width0;
    var index1 = global_idx / width0;
    let width1 = uniforms.output_shape[2] / ${h}u;
    let col = (index1 % width1) * ${h}u;
    index1 = index1 / width1;
    let row = index1 % uniforms.output_shape[1];
    let batch = index1 / uniforms.output_shape[1];

    let x_corner = vec2<i32>(i32(row), i32(col)) * uniforms.strides - uniforms.pads;

    var x_vals: array<${V.type.value}, ${O}>;
    var values: array<${L.type.value}, ${h}>;
    let input_channel = output_channel;
    // Use constant instead of uniform can give better performance for w's height/width.
    for (var w_height: u32 = 0u; w_height < ${$[0]}; w_height++) {
      let x_height = x_corner.x + i32(w_height);
      if (x_height >= 0 && u32(x_height) < uniforms.x_shape[1]) {
        for (var i = 0; i < ${O}; i++) {
          let x_width = x_corner.y + i;
          if (x_width >= 0 && u32(x_width) < uniforms.x_shape[2]) {
            x_vals[i] = ${V.get("batch","u32(x_height)","u32(x_width)","input_channel")};
          } else {
            x_vals[i] = ${V.type.value}(0);
          }
        }
        for (var w_width: u32 = 0u; w_width < ${$[1]}; w_width++) {
          let w_val = ${J.get("w_height","w_width","0","output_channel")};
          for (var i = 0u; i < ${h}u; i++) {
            values[i] = fma(x_vals[i * u32(uniforms.strides[1]) + w_width], w_val, values[i]);
          }
        }
      }
    }

    for (var i = 0u; i < ${h}u; i++) {
      var value = values[i];
      ${se}
      ${W}
      ${L.set("batch","row","col + i","output_channel","value")};
    }
  }`};return{name:"GroupedConv-Vectorize",shaderCache:{hint:`${i.cacheKey};${c};${h};${O};${$[0]};${$[1]}`,inputDependencies:l?["rank","rank","type"]:["rank","rank"]},getRunData:()=>({outputs:[{dims:u?u(n):n,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(y/64)},programUniforms:I}),getShaderSource:M}}}),pc,Au,cc,Ou,$l,vl,hc,fc,xl,Cv=m(()=>{Xe(),Ev(),Iv(),_l(),zv(),ga(),fl(),or(),pc=(t,i,n,u,l,c)=>{let h=t[0],y=t.slice(c?1:2,c?3:4),b=y.length,$=i[0],k=i.slice(2).map((O,M)=>O+(O-1)*(n[M]-1)),I=y.map((O,M)=>O+u[M]+u[M+b]).map((O,M)=>Math.floor((O-k[M]+l[M])/l[M]));return I.splice(0,0,h),I.splice(c?3:1,0,$),I},Au=[2,3,1,0],cc=(t,i)=>{if(!t||t.length!==2&&t.length!==3)throw new Error("Conv requires 2 or 3 inputs");if(t[0].dims.length>5)throw new Error("greater than 5D is not supported");if(t[0].dims.length!==t[1].dims.length)throw new Error("filter does not have same dimension as input");let n=t[0].dims[i.format==="NHWC"?t[0].dims.length-1:1],u=t[1].dims[1]*i.group;if(n!==u)throw new Error("FILTER_IN_CHANNEL should be equal to DATA_CHANNEL");if(t.length===3&&(t[2].dims.length!==1||t[1].dims[0]!==t[2].dims[0]))throw new Error("invalid bias");let l=t[0].dims.length-2;if(i.dilations.length!==l)throw new Error(`dilations should be ${l}D`);if(i.strides.length!==l)throw new Error(`strides should be ${l}D`);if(i.pads.length!==l*2)throw new Error(`pads should be ${l*2}D`);if(i.kernelShape.length!==0&&i.kernelShape.length!==t[1].dims.length-2)throw new Error("invalid kernel shape")},Ou=(t,i)=>{let n=t.kernelShape.slice();n.length<i[1].dims.length-2&&n.push(...Array(i[1].dims.length-2-n.length).fill(0));for(let c=2;c<i[1].dims.length;++c)n[c-2]===0&&(n[c-2]=i[1].dims[c]);let u=t.pads.slice();Fi.adjustPadsBasedOnAutoPad(i[0].dims,t.strides,t.dilations,n,u,t.format==="NHWC",t.autoPad);let l=Object.assign({},t);return Object.assign(l,{kernelShape:n,pads:u}),l},$l=t=>{let i=kn(t),n=t.format,u=["NOTSET","VALID","SAME_UPPER","SAME_LOWER"][t.auto_pad],l=t.dilations,c=t.group,h=t.kernel_shape,y=t.pads,b=t.strides,$=t.w_is_const();return{autoPad:u,format:n,dilations:l,group:c,kernelShape:h,pads:y,strides:b,wIsConst:$,...i,cacheKey:`${t.format};${i.activation};`}},vl=(t,i,n,u)=>{let l=n.format==="NHWC",c=pc(i[0].dims,i[1].dims,n.dilations,n.pads,n.strides,l);if(n.group!==1){let de=[i[0]];if(l){let fe=t.kernelCustomData.wT??t.compute(cr(i[1],Au),{inputs:[1],outputs:[n.wIsConst?-2:-1]})[0];n.wIsConst&&!t.kernelCustomData.wT&&(t.kernelCustomData.wT=fe),de.push(fe)}else de.push(i[1]);i.length===3&&de.push(i[2]),!t.adapterInfo.isArchitecture("ampere")&&l&&i[1].dims[0]===n.group&&i[1].dims[1]===1&&n.dilations[0]===1&&n.dilations[1]===1?t.compute(dc(de,n,c,u),{inputs:de}):t.compute(lc(de,n,c,u),{inputs:de});return}let h=i.length===3,y=i[0].dims[l?1:2],b=i[0].dims[l?2:3],$=i[0].dims[l?3:1],k=i[1].dims[2],I=i[1].dims[3],O=c[l?1:2],M=c[l?2:3],D=c[l?3:1],L=l&&k===y&&I===b&&n.pads[0]===0&&n.pads[1]===0;if(L||k===1&&I===1&&n.dilations[0]===1&&n.dilations[1]===1&&n.strides[0]===1&&n.strides[1]===1&&n.pads[0]===0&&n.pads[1]===0){let de=c[0],fe,we,xe,De=[];if(l){let tt=t.kernelCustomData.wT??t.compute(cr(i[1],Au),{inputs:[1],outputs:[n.wIsConst?-2:-1]})[0];if(n.wIsConst&&!t.kernelCustomData.wT&&(t.kernelCustomData.wT=tt),L){let xt=y*b*$;fe=i[0].reshape([1,de,xt]),we=tt.reshape([1,xt,D]),xe=[1,de,D]}else fe=i[0].reshape([de,y*b,$]),we=tt.reshape([1,$,D]),xe=[de,O*M,D];De.push(fe),De.push(we)}else fe=i[0].reshape([de,$,y*b]),we=i[1].reshape([1,D,$]),xe=[de,D,O*M],De.push(we),De.push(fe);h&&De.push(i[2]);let at=xe[2],et=De[0].dims[De[0].dims.length-1];at<8&&et<8?t.compute(hl(De,n,c,xe,l,u),{inputs:De}):t.compute(Cu(De,n,c,xe,l,u),{inputs:De});return}let Z=!0,W=t.kernelCustomData.wT??t.compute(cr(i[1],Au),{inputs:[1],outputs:[n.wIsConst?-2:-1]})[0];n.wIsConst&&!t.kernelCustomData.wT&&(t.kernelCustomData.wT=W);let V=[i[0],W];h&&V.push(i[2]);let J=l?O*M:D,Y=l?D:O*M,se=k*I*$;t.compute(ic(V,n,c,J,Y,se,h,Z,u),{inputs:V})},hc=(t,i)=>{let n=i.format==="NHWC",u=[t.inputs[0].reshape(n?[t.inputs[0].dims[0],1,t.inputs[0].dims[1],t.inputs[0].dims[2]]:[t.inputs[0].dims[0],t.inputs[0].dims[1],1,t.inputs[0].dims[2]]),t.inputs[1].reshape([t.inputs[1].dims[0],t.inputs[1].dims[1],1,t.inputs[1].dims[2]])];t.inputs.length===3&&u.push(t.inputs[2]);let l=[0,i.pads[0],0,i.pads[1]],c=[1].concat(i.strides),h=[1].concat(i.dilations),y=[1].concat(i.kernelShape),b=Ou({...i,pads:l,strides:c,dilations:h,kernelShape:y},u);vl(t,u,b,$=>n?[$[0],$[2],$[3]]:[$[0],$[1],$[3]])},fc=(t,i,n)=>{let u=n.format==="NHWC"?"channelsLast":"channelsFirst",l=Ou(n,i),c=n.autoPad==="NOTSET"?n.pads:n.autoPad,h=oc(i[0].dims,i[1].dims,n.strides,n.dilations,c,!1,u);t.compute(uc(i,l,h.outShape,[h.filterDepth,h.filterHeight,h.filterWidth],[h.padInfo.front,h.padInfo.top,h.padInfo.left],u))},xl=(t,i)=>{if(cc(t.inputs,i),t.inputs[0].dims.length===3)hc(t,i);else if(t.inputs[0].dims.length===5)fc(t,t.inputs,i);else{let n=Ou(i,t.inputs);vl(t,t.inputs,n)}}}),mc,Av=m(()=>{it(),Mr(),Xe(),Ke(),mc=(t,i,n)=>{let u=t.length>2,l=i.outputShape,c=i.format==="NHWC",h=i.group,y=t[1].dims,b=y[2]/h,$=y[3],k=c?le(b):1,I=c&&$===1&&b>=4,O=I?Math.floor(b/4)*4:Math.floor(b/k)*k,M=b-O,D=c?le($):1,L=c?$===1?k:D:1,Z=he.size(l)/D,W=[Math.ceil(Z/64),1,1];mt("verbose",()=>`[conv2d_backprop_webgpu] dispatch = ${W}`);let V=["rank","rank"],J=[i.strides[0],i.strides[1]],Y=[i.kernelShape[c?1:2],i.kernelShape[c?2:3]],se=[i.dilations[0],i.dilations[1]],de=[Y[0]+(i.dilations[0]<=1?0:(i.kernelShape[c?1:2]-1)*(i.dilations[0]-1)),Y[1]+(i.dilations[1]<=1?0:(i.kernelShape[c?2:3]-1)*(i.dilations[1]-1))],fe=[de[0]-1-Math.floor((i.pads[0]+i.pads[2])/2),de[1]-1-Math.floor((i.pads[1]+i.pads[3])/2)],we=[{type:12,data:Z},{type:12,data:J},{type:12,data:Y},{type:12,data:se},{type:12,data:de},{type:6,data:fe},{type:12,data:O},{type:12,data:b},{type:12,data:$},...ie(t[0].dims,t[1].dims)];u&&(we.push(...ie(t[2].dims)),V.push("rank")),we.push(...ie(l));let xe=De=>{let at=[{name:"output_size",type:"u32"},{name:"strides",type:"u32",length:J.length},{name:"filter_dims",type:"u32",length:Y.length},{name:"dilations",type:"u32",length:Y.length},{name:"effective_filter_dims",type:"u32",length:de.length},{name:"pads",type:"i32",length:fe.length},{name:"input_channels_per_group_int",type:"u32"},{name:"input_channels_per_group",type:"u32"},{name:"output_channels_per_group",type:"u32"}],et=ue(t[0].dataType),tt=c?1:2,xt=c?2:3,zt=c?3:1,rt=oe("W",t[1].dataType,t[1].dims.length,L),ot=oe("Dy",t[0].dataType,t[0].dims.length,k),ur=[ot,rt];u&&ur.push(oe("bias",t[2].dataType,[l[zt]].length,D));let Ne=ke("result",t[0].dataType,l.length,D),Ot=()=>{let Ze="";if(I)k===4?Ze+=`
        let xValue = ${ot.getByOffset("x_offset")};
        let wValue = ${rt.getByOffset("w_offset")};
        dotProd = dotProd + dot(xValue, wValue);
        x_offset += 1u;
        w_offset += 1u;`:k===2?Ze+=`
          dotProd = dotProd + dot(vec4<${et}>(${ot.getByOffset("x_offset")}, ${ot.getByOffset("x_offset + 1u")}), vec4<${et}>(${rt.getByOffset("w_offset")}, ${rt.getByOffset("w_offset + 1u")}));
          x_offset += 2u;
          w_offset += 2u;`:k===1&&(Ze+=`
          dotProd = dotProd + dot(vec4<${et}>(${ot.getByOffset("x_offset")}, ${ot.getByOffset("x_offset + 1u")}, ${ot.getByOffset("x_offset + 2u")}, ${ot.getByOffset("x_offset + 3u")}), vec4<${et}>(${rt.getByOffset("w_offset")}, ${rt.getByOffset("w_offset + 1u")}, ${rt.getByOffset("w_offset + 2u")}, ${rt.getByOffset("w_offset + 3u")}));
          x_offset += 4u;
          w_offset += 4u;`);else if(Ze+=`
                  let xValue = ${c?ot.getByOffset(`${ot.indicesToOffset(`${ot.type.indices}(batch, idyR, idyC, inputChannel)`)} / ${k}`):ot.get("batch","inputChannel","idyR","idyC")};
        `,k===1)Ze+=`
          let w_offset = ${rt.indicesToOffset(`${rt.type.indices}(u32(wRPerm), u32(wCPerm), inputChannel, wOutChannel)`)};
          let wValue = ${rt.getByOffset(`w_offset / ${L}`)};
          dotProd = dotProd + xValue * wValue;`;else for(let Pe=0;Pe<k;Pe++)Ze+=`
            let wValue${Pe} = ${rt.getByOffset(`${rt.indicesToOffset(`${rt.type.indices}(u32(wRPerm), u32(wCPerm), inputChannel + ${Pe}, wOutChannel)`)} / ${L}`)};
            dotProd = dotProd + xValue[${Pe}] * wValue${Pe};`;return Ze},Ae=()=>{if(M===0)return"";if(!I)throw new Error(`packInputAs4 ${I} is not true.`);let Ze="";if(k===1){Ze+="dotProd = dotProd";for(let Pe=0;Pe<M;Pe++)Ze+=`
            + ${ot.getByOffset(`x_offset + ${Pe}`)} * ${rt.getByOffset(`w_offset + ${Pe}`)}`;Ze+=";"}else if(k===2){if(M!==2)throw new Error(`Invalid inputChannelsRemainder ${M}.`);Ze+=`
          let xValue = ${ot.getByOffset("x_offset")};
          let wValue = ${rt.getByOffset("w_offset")};
          dotProd = dotProd + dot(xValue, wValue);`}return Ze},Ge=`
            let outputIndices = ${Ne.offsetToIndices(`global_idx * ${D}`)};
            let batch = ${Ne.indicesGet("outputIndices",0)};
            let d1 = ${Ne.indicesGet("outputIndices",zt)};
            let r = ${Ne.indicesGet("outputIndices",tt)};
            let c = ${Ne.indicesGet("outputIndices",xt)};
            let dyCorner = vec2<i32>(i32(r), i32(c)) - uniforms.pads;
            let dyRCorner = dyCorner.x;
            let dyCCorner = dyCorner.y;
            let groupId = d1 / uniforms.output_channels_per_group;
            let wOutChannel = d1 - groupId * uniforms.output_channels_per_group;
            // Convolve dy(?, ?, d2) with w(:, :, d1, d2) to compute dx(xR, xC, d1).
            // ? = to be determined. : = across all values in that axis.
            var dotProd = ${Ne.type.value}(0.0);
            var wR: u32 = 0;
            if (uniforms.dilations.x == 1) {
              // Minimum wR >= 0 that satisfies (dyRCorner + wR) % (uniforms.strides.x) == 0
              wR = u32(((dyRCorner + i32(uniforms.strides.x) - 1) / i32(uniforms.strides.x)) * i32(uniforms.strides.x) - dyRCorner);
            }
            for (; wR < uniforms.effective_filter_dims.x; wR = wR + 1) {
              if (wR % uniforms.dilations.x != 0) {
                continue;
              }
              let dyR = (${et}(dyRCorner) + ${et}(wR)) / ${et}(uniforms.strides[0]);
              let wRPerm = uniforms.filter_dims.x - 1 - wR / uniforms.dilations.x;
              if (dyR < 0.0 || dyR >= ${et}(uniforms.Dy_shape[${tt}]) || fract(dyR) > 0.0 ||
                  wRPerm < 0) {
                continue;
              }
              let idyR: u32 = u32(dyR);
              var wC: u32 = 0;
              if (uniforms.dilations.y == 1) {
                // Minimum wC >= 0 that satisfies (dyCCorner + wC) % (uniforms.strides.y) == 0
                wC = u32(((dyCCorner + i32(uniforms.strides.y) - 1) / i32(uniforms.strides.y)) * i32(uniforms.strides.y) - dyCCorner);
              }
              for (; wC < uniforms.effective_filter_dims.y; wC = wC + 1) {
                if (wC % uniforms.dilations.y != 0) {
                  continue;
                }
                let dyC = (${et}(dyCCorner) + ${et}(wC)) / ${et}(uniforms.strides.y);
                let wCPerm = uniforms.filter_dims.y - 1 - wC / uniforms.dilations.y;
                if (dyC < 0.0 || dyC >= ${et}(uniforms.Dy_shape[${xt}]) ||
                    fract(dyC) > 0.0 || wCPerm < 0) {
                  continue;
                }
                let idyC: u32 = u32(dyC);
                var inputChannel = groupId * uniforms.input_channels_per_group;
                ${I?`
                var x_offset = ${ot.indicesToOffset(`${ot.type.indices}(batch, idyR, idyC, inputChannel)`)} / ${k};
                var w_offset = ${rt.indicesToOffset(`${rt.type.indices}(wRPerm, wCPerm, inputChannel, wOutChannel)`)} / ${L};
                  `:""}
                for (var d2: u32 = 0; d2 < uniforms.input_channels_per_group_int; d2 = d2 + ${I?4:k}) {
                  ${Ot()}
                  inputChannel = inputChannel + ${I?4:k};
                }
                ${Ae()}
                wC = wC + uniforms.strides.y - 1;
              }
              wR = wR + uniforms.strides[0] - 1;
            }
            let value = dotProd${u?` + bias[d1 / ${D}]`:""};
            ${Ne.setByOffset("global_idx","value")};
          `;return`
    ${De.registerUniforms(at).declareVariables(...ur,Ne)}
      ${De.mainStart()}
      ${De.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")};
    ${Ge}}`};return{name:"ConvTranspose2D",shaderCache:{hint:`${i.cacheKey};${k}${L}${D}${I}${M}`,inputDependencies:V},getRunData:()=>({dispatchGroup:{x:W[0],y:W[1],z:W[2]},outputs:[{dims:n?n(l):l,dataType:t[0].dataType}],programUniforms:we}),getShaderSource:xe}}}),gc,yc,_c,Sl,wc,bc,Tl,$c,vc,Ov=m(()=>{Av(),ga(),or(),gc=(t,i,n,u,l,c)=>(t-1)*i+n+(u-1)*l+1-c,yc=(t,i,n,u,l)=>{let c=Math.floor(t/2);i==="SAME_UPPER"?(n[u]=c,n[l]=t-c):i==="SAME_LOWER"&&(n[u]=t-c,n[l]=c)},_c=(t,i,n,u,l,c,h,y,b,$)=>{let k=t.length-2,I=$.length===0;b.length<k&&b.push(...Array(k-b.length).fill(0));let O=t[0],M=i[y?3:1]*l;for(let D=0,L=t.length-k-(y?1:0);D<k;++D,++L){let Z=t[L],W=I?Z*h[D]:$[D],V=gc(Z,h[D],c[D],i[L],n[D],W);yc(V,u,c,D,D+k),I&&$.push(h[D]*(Z-1)+b[D]+(i[L]-1)*n[D]+1-c[D]-c[D+k])}$.splice(0,0,O),$.splice(y?3:1,0,M)},Sl=(t,i)=>{let n=t.kernelShape.slice();if(t.kernelShape.length===0||t.kernelShape.reduce((I,O)=>I*O,1)===0){n.length=0;for(let I=2;I<i[1].dims.length;++I)n.push(i[1].dims[I])}let u=t.format==="NHWC";n.splice(0,0,i[1].dims[0]),n.splice(u?3:1,0,i[1].dims[1]);let l=t.pads.slice(),c=t.outputShape.slice(),h=t.outputPadding.slice(),y=i[0].dims,b=t.dilations.slice();if(b.reduce((I,O)=>I+O,0)===0){let I=i[0].dims.length-2;b=new Array(I).fill(1)}let $=t.strides.slice();if($.reduce((I,O)=>I+O,0)===0){let I=i[0].dims.length-2;$=new Array(I).fill(1)}_c(y,n,b,t.autoPad,t.group,l,$,u,h,c);let k=Object.assign({},t);return Object.assign(k,{kernelShape:n,pads:l,outputPadding:h,outputShape:c,dilations:b,strides:$}),k},wc=t=>{let i=kn(t),n=t.format,u=["NOTSET","VALID","SAME_UPPER","SAME_LOWER"][typeof t.autoPad>"u"?0:t.autoPad],l=t.dilations,c=t.group??1,h=t.kernelShape,y=t.pads,b=t.strides,$=t.wIsConst(),k=t.outputPadding,I=t.outputShape;return{autoPad:u,format:n,dilations:l,group:c,kernelShape:h,outputPadding:k,outputShape:I,pads:y,strides:b,wIsConst:$,...i,cacheKey:`${t.format};${i.activation};`}},bc=(t,i)=>{if(!t||t.length!==2&&t.length!==3)throw new Error("Conv requires 2 or 3 inputs");if(t[0].dims.length!==4&&t[0].dims.length!==3)throw new Error("currently only support 2-dimensional conv");if(t[0].dims.length!==t[1].dims.length)throw new Error("filter does not have same dimension as input");let n=t[0].dims[i.format==="NHWC"?t[0].dims.length-1:1],u=t[1].dims[0];if(n!==u)throw new Error("FILTER_IN_CHANNEL should be equal to DATA_CHANNEL");let l=t[1].dims[1]*i.group;if(t.length===3&&(t[2].dims.length!==1||t[2].dims[0]!==l))throw new Error("invalid bias");let c=t[0].dims.length-2;if(i.dilations.reduce((h,y)=>h+y,0)>0&&i.dilations.length!==c)throw new Error(`dilations should be ${c}D`);if(i.strides.reduce((h,y)=>h+y,0)>0&&i.strides.length!==c)throw new Error(`strides should be ${c}D`);if(i.pads.reduce((h,y)=>h+y,0)>0&&i.pads.length!==c*2)throw new Error(`pads should be ${c*2}D`);if(i.outputPadding.length!==c&&i.outputPadding.length!==0)throw new Error(`output_padding should be ${c}D`);if(i.kernelShape.reduce((h,y)=>h+y,0)>0&&i.kernelShape.length!==0&&i.kernelShape.length!==t[1].dims.length-2)throw new Error("invalid kernel shape");if(i.outputShape.length!==0&&i.outputShape.length!==t[0].dims.length-2)throw new Error("invalid output shape")},Tl=(t,i,n,u)=>{let l=t.kernelCustomData.wT??t.compute(cr(i[1],[2,3,0,1]),{inputs:[1],outputs:[n.wIsConst?-2:-1]})[0];n.wIsConst&&!t.kernelCustomData.wT&&(t.kernelCustomData.wT=l);let c=[i[0],l];i.length===3&&c.push(i[2]),t.compute(mc(c,n,u),{inputs:c})},$c=(t,i)=>{let n=i.format==="NHWC",u=[t.inputs[0].reshape(n?[t.inputs[0].dims[0],1,t.inputs[0].dims[1],t.inputs[0].dims[2]]:[t.inputs[0].dims[0],t.inputs[0].dims[1],1,t.inputs[0].dims[2]]),t.inputs[1].reshape([t.inputs[1].dims[0],t.inputs[1].dims[1],1,t.inputs[1].dims[2]])];t.inputs.length===3&&u.push(t.inputs[2]);let l=i.kernelShape;(l.length===0||l[0]===0)&&(l=[t.inputs[1].dims[2]]);let c=i.dilations;(c.length===0||c[0]===0)&&(c=[1]);let h=i.strides;(h.length===0||h[0]===0)&&(h=[1]);let y=i.pads;y.length===0&&(y=[0,0]),y=[0,y[0],0,y[1]],h=[1].concat(h),c=[1].concat(c),l=[1].concat(l);let b=i.outputPadding;b=[0].concat(b);let $=Sl({...i,pads:y,strides:h,dilations:c,kernelShape:l,outputPadding:b},u);Tl(t,u,$,k=>n?[k[0],k[2],k[3]]:[k[0],k[1],k[3]])},vc=(t,i)=>{if(bc(t.inputs,i),t.inputs[0].dims.length===3)$c(t,i);else{let n=Sl(i,t.inputs);Tl(t,t.inputs,n)}}}),xc,Sc,Tc,Rv=m(()=>{it(),Xe(),j(),Ke(),xc=(t,i,n,u)=>{let l=he.size(i),c=i.length,h=oe("input",t,c),y=ke("output",t,c),b=n.dataType===6?n.getInt32Array()[0]:Number(n.getBigInt64Array()[0]),$=he.normalizeAxis(b,c),k=I=>{let O=` i32(${h.indicesGet("inputIndices","uniforms.axis")}) `,M=ce("uniforms.input_shape","uniforms.axis",c),D=u.reverse?O+(u.exclusive?" + 1":""):"0",L=u.reverse?M:O+(u.exclusive?"":" + 1");return`
                ${I.registerUniform("outputSize","u32").registerUniform("axis","u32").declareVariables(h,y)}
                ${I.mainStart()}
                  ${I.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
                  var inputIndices = ${y.offsetToIndices("global_idx")};
                  var sum = ${y.type.value}(0);
                  let first : i32 = ${D};
                  let last : i32 = ${L};
                  for (var i : i32 = first; i < last; i++) {
                    ${h.indicesSet("inputIndices","uniforms.axis","u32(i)")};
                    sum = sum + ${h.getByIndices("inputIndices")};
                  }
                  ${y.setByOffset("global_idx","sum")};
                }`};return{name:"CumSum",shaderCache:{hint:u.cacheKey,inputDependencies:["rank"]},getRunData:()=>({outputs:[{dims:i,dataType:t}],dispatchGroup:{x:Math.ceil(l/64)},programUniforms:[{type:12,data:l},{type:12,data:$},...ie(i,i)]}),getShaderSource:k}},Sc=(t,i)=>{let n=t.inputs[0].dims,u=t.inputs[0].dataType,l=t.inputs[1];t.compute(xc(u,n,l,i),{inputs:[0]})},Tc=t=>{let i=t.exclusive===1,n=t.reverse===1;return N({exclusive:i,reverse:n})}}),kc,Ec,Ic,zc,Cc,Bv=m(()=>{it(),Xe(),j(),Ke(),kc=t=>{if(!t||t.length!==1)throw new Error("DepthToSpace requires 1 input.");if(t[0].dims.length!==4)throw new Error("DepthToSpace requires 4D input.")},Ec=(t,i,n,u)=>{let l=[];l.push(`fn perm(i: ${u.type.indices}) -> ${n.type.indices} {
    var a: ${n.type.indices};`);for(let c=0;c<i;++c)l.push(n.indicesSet("a",t[c],`i[${c}]`));return l.push("return a;}"),l.join(`
`)},Ic=(t,i)=>{let n,u,l,c,h,y,b=i.format==="NHWC",$=i.blocksize,k=i.mode==="DCR";b?([n,u,l,c]=t.dims,h=k?[n,u,l,$,$,c/$**2]:[n,u,l,c/$**2,$,$],y=k?[0,1,3,2,4,5]:[0,1,4,2,5,3]):([n,u,l,c]=[t.dims[0],t.dims[2],t.dims[3],t.dims[1]],h=k?[n,$,$,c/$**2,u,l]:[n,c/$**2,$,$,u,l],y=k?[0,3,4,1,5,2]:[0,1,4,2,5,3]);let I=t.reshape(h),O=I.dims.length,M=t.dataType,D=oe("a",M,O),L=ke("output",M,O),Z=W=>`
  ${W.registerUniform("output_size","u32").declareVariables(D,L)}

  ${Ec(y,O,D,L)}

  ${W.mainStart()}
    ${W.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let indices = ${L.offsetToIndices("global_idx")};
    let aIndices = perm(indices);

    ${L.setByOffset("global_idx",D.getByIndices("aIndices"))}
  }`;return{name:"DepthToSpace",shaderCache:{hint:`${t.dims};${i.blocksize};${i.mode}`,inputDependencies:["rank"]},getRunData:W=>{let V=b?[n,u*$,l*$,c/$**2]:[n,c/$**2,u*$,l*$],J=he.size(V),Y=I.dims,se=he.sortBasedOnPerm(Y,y);return{outputs:[{dims:V,dataType:W[0].dataType}],dispatchGroup:{x:Math.ceil(J/64)},programUniforms:[{type:12,data:J},...ie(Y,se)]}},getShaderSource:Z}},zc=(t,i)=>{kc(t.inputs),t.compute(Ic(t.inputs[0],i))},Cc=t=>N({blocksize:t.blocksize,mode:t.mode,format:t.format})}),Ru,fo,kl,Ac,Oc,Rc,Bc,El,Mc,Dc,Nc,Mv=m(()=>{it(),Xe(),j(),Ke(),Ru="[a-zA-Z]|\\.\\.\\.",fo="("+Ru+")+",kl="^"+fo+"$",Ac="("+fo+",)*"+fo,Oc="^"+Ac+"$",Rc=class{constructor(t=-1){this.symbolToIndices=new Map,this.inputIndex=t}addSymbol(t,i){let n=this.symbolToIndices.get(t);n===void 0?n=[i]:n.push(i),this.symbolToIndices.set(t,n)}},Bc=class{constructor(t,i){var l;this.equation=i,this.hasEllipsis=!1,this.symbolToInfo=new Map,this.lhs=new Array,this.outputDims=[];let[n,u]=i.includes("->")?i.split("->",2):[i,""];if(!n.match(RegExp(Oc)))throw new Error("Invalid LHS term");if(n.split(",").forEach((c,h)=>{let y=t[h].dims.slice();if(!c.match(RegExp(kl)))throw new Error("Invalid LHS term");let b=this.processTerm(c,!0,y,h);this.lhs.push(b)}),u==="")u+=[...this.symbolToInfo.entries()].filter(([c,h])=>h.count===1||c==="...").map(([c])=>c).join("");else if(!u.match(RegExp(fo)))throw new Error("Invalid RHS");(l=u.match(RegExp(Ru,"g")))==null||l.forEach(c=>{if(c==="...")this.outputDims=this.outputDims.concat(this.ellipsisDims);else{let h=this.symbolToInfo.get(c);if(h===void 0)throw new Error("Invalid RHS symbol");this.outputDims.push(h.dimValue)}}),this.rhs=this.processTerm(u,!1,this.outputDims)}addSymbol(t,i,n){let u=this.symbolToInfo.get(t);if(u!==void 0){if(u.dimValue!==i&&u.count!==1)throw new Error("Dimension mismatch");u.count++,u.inputIndices.push(n)}else u={count:1,dimValue:i,inputIndices:[n]};this.symbolToInfo.set(t,u)}processTerm(t,i,n,u=-1){let l=n.length,c=!1,h=[],y=0;if(!t.match(RegExp(kl))&&!i&&t!=="")throw new Error("Invalid LHS term");let b=t.match(RegExp(Ru,"g")),$=new Rc(u);return b==null||b.forEach((k,I)=>{if(k==="..."){if(c)throw new Error("Only one ellipsis is allowed per input term");c=!0;let O=l-b.length+1;if(O<0)throw new Error("Ellipsis out of bounds");if(h=n.slice(y,y+O),this.hasEllipsis){if(this.ellipsisDims.length!==h.length||this.ellipsisDims.toString()!==h.toString())throw new Error("Ellipsis dimensions mismatch")}else if(i)this.hasEllipsis=!0,this.ellipsisDims=h;else throw new Error("Ellipsis must be specified in the LHS");for(let M=0;M<h.length;M++){let D=String.fromCharCode(48+M);$.addSymbol(D,I+M),this.addSymbol(D,n[y++],u)}}else $.addSymbol(k,I+(this.hasEllipsis?this.ellipsisDims.length-1:0)),this.addSymbol(k,n[y++],u)}),$}},El=t=>t+"_max",Mc=(t,i,n,u)=>{let l=t.map($=>$.length).map(($,k)=>oe(`input${k}`,i,$)),c=he.size(u),h=ke("output",i,u.length),y=[...n.symbolToInfo.keys()].filter($=>!n.rhs.symbolToIndices.has($)),b=$=>{let k=[],I="var prod = 1.0;",O="var sum = 0.0;",M="sum += prod;",D=[],L=[],Z=[],W=[],V=n.symbolToInfo.size===n.rhs.symbolToIndices.size;n.symbolToInfo.forEach((Y,se)=>{var de;if(n.rhs.symbolToIndices.has(se)){let fe=(de=n.rhs.symbolToIndices.get(se))==null?void 0:de[0];fe!==void 0&&n.lhs.forEach((we,xe)=>{if(Y.inputIndices.includes(xe)){let De=we.symbolToIndices.get(se);if(De===void 0)throw new Error("Invalid symbol error");De.forEach(at=>{k.push(`${l[xe].indicesSet(`input${xe}Indices`,at,h.indicesGet("outputIndices",fe))}`)})}})}else n.lhs.forEach((fe,we)=>{if(Y.inputIndices.includes(we)){let xe=fe.symbolToIndices.get(se);if(xe===void 0)throw new Error("Invalid symbol error");xe.forEach(De=>{D.push(`${l[we].indicesSet(`input${we}Indices`,De,`${se}`)}`)}),W.push(`prod *= ${l[we].getByIndices(`input${we}Indices`)};`)}}),L.push(`for(var ${se}: u32 = 0; ${se} < uniforms.${El(se)}; ${se}++) {`),Z.push("}")});let J=V?[...k,`let sum = ${l.map((Y,se)=>Y.getByIndices(`input${se}Indices`)).join(" * ")};`]:[...k,O,...L,...D,I,...W,M,...Z];return`
            ${$.registerUniforms(y.map(Y=>({name:`${El(Y)}`,type:"u32"}))).registerUniform("outputSize","u32").declareVariables(...l,h)}

            ${$.mainStart()}
            ${$.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
            var outputIndices = ${h.offsetToIndices("global_idx")};
            ${l.map((Y,se)=>`var input${se}Indices: ${l[se].type.indices};`).join(`
`)}
            ${J.join(`
`)};
            ${h.setByOffset("global_idx","sum")};
          }`};return{name:"Einsum",shaderCache:{hint:n.equation,inputDependencies:t.map(()=>"rank")},getRunData:()=>{let $=y.filter(I=>n.symbolToInfo.has(I)).map(I=>{var O;return{type:12,data:((O=n.symbolToInfo.get(I))==null?void 0:O.dimValue)||0}});$.push({type:12,data:c});let k=t.map((I,O)=>[...ie(I)]).reduce((I,O)=>I.concat(O),$);return k.push(...ie(u)),{outputs:[{dims:u,dataType:i}],dispatchGroup:{x:Math.ceil(c/64)},programUniforms:k}},getShaderSource:b}},Dc=(t,i)=>{let n=new Bc(t.inputs,i.equation),u=n.outputDims,l=t.inputs.map((c,h)=>c.dims);t.compute(Mc(l,t.inputs[0].dataType,n,u))},Nc=t=>{let i=t.equation.replace(/\s+/g,"");return N({equation:i})}}),Pc,Il,Uc,Lc,qc,Dv=m(()=>{it(),Xe(),Ke(),Pc=t=>{if(!t||t.length!==2)throw new Error("Expand requires 2 input.");let i=t[0].dims,n=Array.from(t[1].getBigInt64Array(),Number),u=n.length<i.length?0:n.length-i.length,l=i.length<n.length?0:i.length-n.length;for(;u<n.length&&l<i.length;++u,++l)if(n[u]!==i[l]&&n[u]!==1&&i[l]!==1)throw new Error("Expand requires shape to be broadcastable to input")},Il=(t,i)=>{let n=t.length-i.length,u=[];for(let l=0;l<n;++l)u.push(t[l]);for(let l=0;l<i.length;++l)u.push(i[l]===1?t[l+n]:i[l]);return u},Uc=(t,i)=>t.length>i.length?Il(t,i):Il(i,t),Lc=t=>{let i=t[0].dims,n=Array.from(t[1].getBigInt64Array(),Number),u=Uc(i,n),l=t[0].dataType,c=l===9||he.size(i)===1,h=l===9||i.length>0&&i[i.length-1]%4===0?4:1,y=c||u.length>0&&u[u.length-1]%4===0?4:1,b=Math.ceil(he.size(u)/y),$=I=>{let O=oe("input",l,i.length,h),M=ke("output",l,u.length,y),D;if(l===9){let L=(Z,W,V="")=>`
          let outputIndices${W} = ${M.offsetToIndices(`outputOffset + ${W}u`)};
          let offset${W} = ${O.broadcastedIndicesToOffset(`outputIndices${W}`,M)};
          let index${W} = offset${W} / 4u;
          let component${W} = offset${W} % 4u;
          ${Z}[${W}] = ${V}(${O.getByOffset(`index${W}`)}[component${W}]);
        `;D=`
        let outputOffset = global_idx * ${y};
        var data = vec4<u32>(0);
        ${L("data",0,"u32")}
        ${L("data",1,"u32")}
        ${L("data",2,"u32")}
        ${L("data",3,"u32")}
        ${M.setByOffset("global_idx","data")}
      }`}else D=`
        let outputIndices = ${M.offsetToIndices(`global_idx * ${y}`)};
        let inputOffset = ${O.broadcastedIndicesToOffset("outputIndices",M)};
        let data = ${M.type.value}(${O.getByOffset(`inputOffset / ${h}`)});
        ${M.setByOffset("global_idx","data")}
      }`;return`
    ${I.registerUniform("vec_size","u32").declareVariables(O,M)}
    ${I.mainStart()}
    ${I.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}
    ${D}`},k=[{type:12,data:b},...ie(i,u)];return{name:"Expand",shaderCache:{hint:`${u.length};${h}${y}`,inputDependencies:["rank"]},getShaderSource:$,getRunData:()=>({outputs:[{dims:u,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(b/64)},programUniforms:k})}},qc=t=>{Pc(t.inputs),t.compute(Lc(t.inputs),{inputs:[0]})}}),Vc,Wc,Nv=m(()=>{it(),Xe(),Ke(),uo(),Vc=t=>{let i=t[0].dataType,n=he.size(t[0].dims),u=he.size(t[1].dims),l=u%4===0,c=h=>{let y=oe("x",i,[1],4),b=oe("bias",i,[1],4),$=ke("y",i,[1],4),k=[{name:"output_vec_size",type:"u32"},{name:"bias_size",type:"u32"}],I=M=>`
      let bias${M}_offset: u32 = (global_idx * 4 + ${M}) % uniforms.bias_size;
      let bias${M} = ${b.getByOffset(`bias${M}_offset / 4`)}[bias${M}_offset % 4];`,O=l?`
      let bias = ${b.getByOffset("global_idx % (uniforms.bias_size / 4)")};`:`${I(0)}${I(1)}${I(2)}${I(3)}
      let bias = ${y.type.value}(bias0, bias1, bias2, bias3);`;return`${h.registerUniforms(k).declareVariables(y,b,$)}

    ${so(re(i))}

    ${h.mainStart(te)}
      ${h.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_vec_size")}

      let x = ${y.getByOffset("global_idx")};
      ${O}
      let x_in = x + bias;
      ${$.setByOffset("global_idx",oo("x_in"))}
    }`};return{name:"FastGeluWithBias",shaderCache:{hint:`${l}`,inputDependencies:["type","type"]},getShaderSource:c,getRunData:h=>({outputs:[{dims:h[0].dims,dataType:h[0].dataType}],programUniforms:[{type:12,data:Math.ceil(n/4)},{type:12,data:u}],dispatchGroup:{x:Math.ceil(n/te/4)}})}},Wc=t=>{t.inputs.length<2||he.size(t.inputs[1].dims)===0?gu(t):t.compute(Vc(t.inputs))}}),Gc,Fc,Hc,jc,Pv=m(()=>{it(),Xe(),j(),Ke(),Gc=t=>{if(!t||t.length!==2)throw new Error("Gather requires 2 inputs.")},Fc=(t,i)=>{let n=t[0].dims,u=t[1].dims,l=n.length,c=he.normalizeAxis(i.axis,l),h=n.slice(0);h.splice(c,1,...u);let y=n[c],b=t[0].dataType===9?4:1,$=Math.ceil(he.size(h)/b),k=[{type:12,data:$},{type:6,data:y},{type:12,data:c},...ie(t[0].dims,t[1].dims,h)],I=O=>{let M=oe("data",t[0].dataType,t[0].dims.length,b),D=oe("inputIndices",t[1].dataType,t[1].dims.length),L=ke("output",t[0].dataType,h.length,b),Z=V=>{let J=u.length,Y=`var indicesIndices${V}  = ${D.type.indices}(0);`;for(let se=0;se<J;se++)Y+=`${J>1?`indicesIndices${V}[${se}]`:`indicesIndices${V}`} = ${h.length>1?`outputIndices${V}[uniforms.axis + ${se}]`:`outputIndices${V}`};`;Y+=`
          var idx${V} = ${D.getByIndices(`indicesIndices${V}`)};
          if (idx${V} < 0) {
            idx${V} = idx${V} + uniforms.axisDimLimit;
          }
          var dataIndices${V} : ${M.type.indices};
        `;for(let se=0,de=0;se<l;se++)se===c?(Y+=`${l>1?`dataIndices${V}[${se}]`:`dataIndices${V}`} = u32(idx${V});`,de+=J):(Y+=`${l>1?`dataIndices${V}[${se}]`:`dataIndices${V}`} = ${h.length>1?`outputIndices${V}[${de}]`:`outputIndices${V}`};`,de++);return Y},W;if(t[0].dataType===9){let V=(J,Y,se="")=>`
          let outputIndices${Y} = ${L.offsetToIndices(`outputOffset + ${Y}u`)};
          ${Z(Y)};
          let offset${Y} = ${M.indicesToOffset(`dataIndices${Y}`)};
          let index${Y} = offset${Y} / 4u;
          let component${Y} = offset${Y} % 4u;
          ${J}[${Y}] = ${se}(${M.getByOffset(`index${Y}`)}[component${Y}]);
        `;W=`
        let outputOffset = global_idx * ${b};
        var value = vec4<u32>(0);
        ${V("value",0,"u32")}
        ${V("value",1,"u32")}
        ${V("value",2,"u32")}
        ${V("value",3,"u32")}
        ${L.setByOffset("global_idx","value")}
      `}else W=`
      let outputIndices = ${L.offsetToIndices("global_idx")};
      ${Z("")};
      let value = ${M.getByIndices("dataIndices")};
      ${L.setByOffset("global_idx","value")};
      `;return`
      ${O.registerUniform("outputSize","u32").registerUniform("axisDimLimit","i32").registerUniform("axis","u32").declareVariables(M,D,L)}
      ${O.mainStart()}
        ${O.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
        ${W}
      }`};return{name:"Gather",shaderCache:{hint:i.cacheKey,inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:h,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil($/64)},programUniforms:k}),getShaderSource:I}},Hc=t=>N({axis:t.axis}),jc=(t,i)=>{let n=t.inputs;Gc(n),t.compute(Fc(t.inputs,i))}}),Kc,Zc,Qc,Uv=m(()=>{it(),Xe(),Ke(),Kc=(t,i,n,u,l,c,h,y,b)=>{let $=[{type:12,data:c},{type:12,data:u},{type:12,data:l},{type:12,data:n},{type:12,data:h},{type:12,data:y},{type:12,data:b}],k=[c];$.push(...ie(i.dims,k));let I=O=>{let M=oe("indices_data",i.dataType,i.dims.length),D=ke("input_slice_offsets_data",12,1,1),L=[M,D],Z=[{name:"output_size",type:"u32"},{name:"batch_dims",type:"u32"},{name:"input_dims",type:"u32",length:l.length},{name:"sizes_from_slice_dims_data",type:"u32",length:n.length},{name:"num_slices_per_batch",type:"u32"},{name:"input_batch_stride",type:"u32"},{name:"num_slice_dims",type:"u32"}];return`
  ${O.registerUniforms(Z).declareVariables(...L)}
  ${O.mainStart()}
    ${O.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let batch_idx = global_idx / uniforms.num_slices_per_batch;
    let base_offset = batch_idx * uniforms.input_batch_stride;

    let slice_indices_base_offset = global_idx * uniforms.num_slice_dims;
    var relative_slice_offset = 0;
    for (var dim_idx = 0u; dim_idx < uniforms.num_slice_dims; dim_idx ++) {
      var index = i32(indices_data[dim_idx + slice_indices_base_offset].x);
      let input_dim_idx = uniforms.batch_dims + dim_idx;
      if (index < 0) {
        ${l.length===1?"index += i32(uniforms.input_dims);":"index += i32(uniforms.input_dims[input_dim_idx]);"}
      }
      ${n.length===1?"relative_slice_offset += index * i32(uniforms.sizes_from_slice_dims_data);":"relative_slice_offset += index * i32(uniforms.sizes_from_slice_dims_data[dim_idx]);"}
    }

    input_slice_offsets_data[global_idx] =  base_offset + u32(relative_slice_offset);
  }`};return t.compute({name:"computeSliceOffsets",shaderCache:{hint:`${l.length}_${n.length}`,inputDependencies:["rank"]},getRunData:()=>({outputs:[{dims:k,dataType:t.inputs[1].dataType}],dispatchGroup:{x:Math.ceil(c/64)},programUniforms:$}),getShaderSource:I},{inputs:[i],outputs:[-1]})[0]},Zc=(t,i)=>{let n=t.inputs,u=n[0].dims,l=n[0].dataType,c=n[1].dims,h=c[c.length-1],y=he.sizeToDimension(c,c.length-1),b=he.sizeFromDimension(u,i.batchDims+h),$=he.sizeToDimension(u,i.batchDims),k=he.sizeFromDimension(u,i.batchDims),I=y/$,O=new Array(h),M=b;for(let Y=0;Y<h;++Y)O[h-1-Y]=M,M*=u[i.batchDims+h-1-Y];let D=Kc(t,n[1],O,i.batchDims,u,y,I,k,h),L=i.batchDims+h;if(L>u.length)throw new Error("last dimension of indices must not be larger than rank of input tensor");let Z=c.slice(0,-1).concat(u.slice(L)),W=he.size(Z),V=[{type:12,data:W},{type:12,data:b},...ie(n[0].dims,D.dims,Z)],J=Y=>{let se=oe("data",n[0].dataType,n[0].dims.length),de=oe("slice_offsets",12,D.dims.length),fe=ke("output",n[0].dataType,Z.length);return`
          ${Y.registerUniform("output_size","u32").registerUniform("slice_size","u32").declareVariables(se,de,fe)}
            ${Y.mainStart()}
            ${Y.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
          let slice_offset = slice_offsets[global_idx / uniforms.slice_size];
          output[global_idx] = data[u32(slice_offset) + global_idx % uniforms.slice_size];
        }`};t.compute({name:"GatherND",shaderCache:{hint:i.cacheKey,inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:Z,dataType:l}],dispatchGroup:{x:Math.ceil(W/64)},programUniforms:V}),getShaderSource:J},{inputs:[n[0],D]})},Qc=t=>({batchDims:t.batch_dims,cacheKey:""})}),Xc,Yc,Jc,eh,Lv=m(()=>{it(),Xe(),j(),Ke(),Xc=(t,i)=>{if(t.length<3||t.length>4)throw new Error("GatherBlockQuantized requires 3 or 4 inputs.");let n=he.normalizeAxis(i.quantizeAxis,t[0].dims.length),u=i.blockSize,l=t[0],c=t[2],h=t.length===4?t[3]:void 0;if(c.dims.length!==l.dims.length||!l.dims.map((y,b)=>b===n?Math.ceil(y/u)===c.dims[b]:y===c.dims[b]).reduce((y,b)=>y&&b,!0))throw new Error("Scales must have the same rank as the input tensor and the dims should match except on gatherAxis.");if(h){if(h.dataType!==l.dataType)throw new Error("Zero point must have the same data type as the input tensor.");if(h.dims.length!==c.dims.length||!h.dims.map((y,b)=>y===c.dims[b]).reduce((y,b)=>y&&b,!0))throw new Error("Zero point must have the same rank as the input tensor and the dims should match except on quantizeAxis.")}},Yc=(t,i)=>{let n=t[0].dims,u=t[1].dims,l=n.length,c=he.normalizeAxis(i.gatherAxis,l),h=he.normalizeAxis(i.quantizeAxis,l),y=n.slice(0);y.splice(c,1,...u);let b=he.size(y),$=t[2].dataType,k=t[0].dataType===22,I=[{type:12,data:b},{type:12,data:h},{type:12,data:c},{type:12,data:i.blockSize},...ie(...t.map((M,D)=>M.dims),y)],O=M=>{let D=oe("data",t[0].dataType,t[0].dims.length),L=oe("inputIndices",t[1].dataType,t[1].dims.length),Z=oe("scales",t[2].dataType,t[2].dims.length),W=t.length>3?oe("zeroPoint",t[3].dataType,t[3].dims.length):void 0,V=ke("output",$,y.length),J=[D,L,Z];W&&J.push(W);let Y=[{name:"output_size",type:"u32"},{name:"quantize_axis",type:"u32"},{name:"gather_axis",type:"u32"},{name:"block_size",type:"u32"}];return`
        ${M.registerUniforms(Y).declareVariables(...J,V)}
        ${M.mainStart()}
        let output_indices = ${V.offsetToIndices("global_idx")};
        var indices_indices = ${L.type.indices}(0);
        ${u.length>1?`
          for (var i: u32 = 0; i < ${u.length}; i++) {
            let index = ${V.indicesGet("output_indices","uniforms.gather_axis + i")};
            ${L.indicesSet("indices_indices","i","index")};
          }`:`indices_indices = ${V.indicesGet("output_indices","uniforms.gather_axis")};`};
        var data_indices = ${D.type.indices}(0);
        for (var i: u32 = 0; i < uniforms.gather_axis; i++) {
          let index = ${V.indicesGet("output_indices","i")};
          ${D.indicesSet("data_indices","i","index")};
        }
        var index_from_indices = ${L.getByIndices("indices_indices")};
        if (index_from_indices < 0) {
          index_from_indices += ${n[c]};
        }
        ${D.indicesSet("data_indices","uniforms.gather_axis","u32(index_from_indices)")};
        for (var i = uniforms.gather_axis + 1; i < ${y.length}; i++) {
          let index = ${V.indicesGet("output_indices",`i + ${u.length} - 1`)};
          ${D.indicesSet("data_indices","i","index")};
        }
        let data_offset = ${D.indicesToOffset("data_indices")};
        let data_index = data_offset % 8;
        // Convert 4-bit packed data to 8-bit packed data.
        let packed_4bit_quantized_data = ${D.getByOffset("data_offset / 8")};
        let packed_8bit_quantized_data = (packed_4bit_quantized_data >> (4 * (data_index % 2))) & 0x0f0f0f0f;
        let quantized_data_vec = ${k?"unpack4xI8":"unpack4xU8"}(u32(packed_8bit_quantized_data));
        let quantized_data = quantized_data_vec[data_index / 2];
        var scale_indices = data_indices;
        let quantize_axis_index = ${Z.indicesGet("data_indices","uniforms.quantize_axis")} / uniforms.block_size;
        ${Z.indicesSet("scale_indices","uniforms.quantize_axis","quantize_axis_index")};
        var scale = ${Z.getByIndices("scale_indices")};
        ${W?`
              let zero_point_indices = scale_indices;
              let zero_point_offset = ${W.indicesToOffset("zero_point_indices")};
              let zero_point_index = zero_point_offset % 8;
              let packed_4bit_zero_points = ${W.getByOffset("zero_point_offset / 8")};
              let packed_8bit_zero_points = (packed_4bit_zero_points >> (4 * (zero_point_index % 2))) & 0x0f0f0f0f;
              let zero_point_vec = ${k?"unpack4xI8":"unpack4xU8"}(u32(packed_8bit_zero_points));
              let zero_point = zero_point_vec[zero_point_index / 2];`:"var zero_point = 0"};
        let dequantized_data = ${re($)}(quantized_data - zero_point) * scale;
        ${V.setByOffset("global_idx","dequantized_data")};
    }`};return{name:"GatherBlockQuantized",shaderCache:{hint:`${i.cacheKey};${t.filter((M,D)=>D!==1).map(M=>M.dims.join("_")).join(";")}`,inputDependencies:Array.from({length:t.length},(M,D)=>"rank")},getRunData:()=>({outputs:[{dims:y,dataType:$}],dispatchGroup:{x:Math.ceil(b/64)},programUniforms:I}),getShaderSource:O}},Jc=(t,i)=>{let n=t.inputs;Xc(n,i),t.compute(Yc(t.inputs,i))},eh=t=>N({blockSize:t.blockSize,gatherAxis:t.gatherAxis,quantizeAxis:t.quantizeAxis})}),th,rh,ih,ah,qv=m(()=>{it(),Xe(),j(),Ke(),th=t=>{if(!t||t.length!==2)throw new Error("GatherElements requires 2 inputs.");if(t[0].dims.length<1)throw new Error("GatherElements requires that the data input be rank >= 1.");if(t[0].dims.length!==t[1].dims.length)throw new Error(`GatherElements requires that the data input and
                     indices input tensors be of same rank.`)},rh=(t,i)=>{let n=t[0].dims,u=t[0].dataType,l=n.length,c=t[1].dims,h=t[1].dataType,y=he.normalizeAxis(i.axis,l),b=n[y],$=c.slice(0),k=he.size($),I=oe("input",u,l),O=oe("indicesInput",h,c.length),M=ke("output",u,$.length),D=[{type:12,data:k},{type:6,data:b},{type:12,data:y}];return D.push(...ie(n,c,$)),{name:"GatherElements",shaderCache:{inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:$,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(k/64)},programUniforms:D}),getShaderSource:L=>`
      ${L.registerUniform("outputSize","u32").registerUniform("axisDimLimit","i32").registerUniform("axis","u32").declareVariables(I,O,M)}
      ${L.mainStart()}
      ${L.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}

      let outputIndices = ${M.offsetToIndices("global_idx")};

      var idx = ${O.getByOffset("global_idx")};
      if (idx < 0) {
        idx = idx + uniforms.axisDimLimit;
      }
      var inputIndices = ${I.type.indices}(outputIndices);
      ${I.indicesSet("inputIndices","uniforms.axis","u32(idx)")};
      let value = ${I.getByIndices("inputIndices")};

      ${M.setByOffset("global_idx","value")};
  }`}},ih=t=>N({axis:t.axis}),ah=(t,i)=>{let n=t.inputs;th(n),t.compute(rh(t.inputs,i))}}),nh,sh,oh,uh,Vv=m(()=>{it(),Xe(),Ke(),nh=t=>{if(!t)throw new Error("Input is missing");if(t.length<2||t.length>3)throw new Error("Invaid input number.");if(t.length===3&&t[2].dims.length>2)throw new Error("Invalid input shape of C");if(t[0].dataType!==t[1].dataType||t.length===3&&t[0].dataType!==t[2].dataType)throw new Error("Input types are mismatched")},sh=(t,i)=>{let n=t[0].dims.slice(),u=t[1].dims.slice(),[l,c,h]=Da.getShapeOfGemmResult(n,i.transA,u,i.transB,t.length===3?t[2].dims:void 0),y=[l,c];if(!y)throw new Error("Can't use gemm on the given tensors");let b=16,$=Math.ceil(c/b),k=Math.ceil(l/b);he.size(y);let I=[{type:12,data:$},{type:12,data:l},{type:12,data:c},{type:12,data:h},{type:1,data:i.alpha},{type:1,data:i.beta}],O=["type","type"];t.length===3&&(I.push(...ie(t[2].dims)),O.push("rank")),I.push(...ie(y));let M=D=>{let L=oe("a",t[0].dataType,t[0].dims),Z=oe("b",t[1].dataType,t[1].dims),W=null,V=[L,Z];t.length===3&&(W=oe("c",t[2].dataType,t[2].dims.length),V.push(W));let J=ke("output",t[0].dataType,y.length);V.push(J);let Y=[{name:"num_tile_n",type:"u32"},{name:"M",type:"u32"},{name:"N",type:"u32"},{name:"K",type:"u32"},{name:"alpha",type:"f32"},{name:"beta",type:"f32"}],se="",de="";i.transA&&i.transB?(de=`
      var col = tile_row_start + local_id.x;
      var row = k_start + local_id.y;
      if (col < uniforms.M && row < uniforms.K) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.M + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${L.type.value}(0);
      }

      col = k_start + local_id.x;
      row = tile_col_start + local_id.y;
      if (col < uniforms.K && row < uniforms.N) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.K + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${Z.type.value}(0);
      }
      `,se="value += tile_a[k][local_id.y] * tile_b[local_id.x][k];"):i.transA&&!i.transB?(de=`
      var col = tile_row_start + local_id.x;
      var row = k_start + local_id.y;
      if (col < uniforms.M && row < uniforms.K) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.M + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${L.type.value}(0);
      }

      col = tile_col_start + local_id.x;
      row = k_start + local_id.y;
      if (col < uniforms.N && row < uniforms.K) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.N + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${Z.type.value}(0);
      }
      `,se="value += tile_a[k][local_id.y] * tile_b[k][local_id.x];"):!i.transA&&i.transB?(de=`
      var col = k_start + local_id.x;
      var row = tile_row_start + local_id.y;
      if (col < uniforms.K && row < uniforms.M) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.K + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${L.type.value}(0);
      }

      col = k_start + local_id.x;
      row = tile_col_start + local_id.y;
      if (col < uniforms.K && row < uniforms.N) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.K + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${Z.type.value}(0);
      }
      `,se="value += tile_a[local_id.y][k] * tile_b[local_id.x][k];"):!i.transA&&!i.transB&&(de=`
      var col = k_start + local_id.x;
      var row = tile_row_start + local_id.y;
      if (col < uniforms.K && row < uniforms.M) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.K + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${L.type.value}(0);
      }

      col = tile_col_start + local_id.x;
      row = k_start + local_id.y;
      if (col < uniforms.N && row < uniforms.K) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.N + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${Z.type.value}(0);
      }
      `,se="value += tile_a[local_id.y][k] * tile_b[k][local_id.x];");let fe=i.alpha===1?"":"value *= uniforms.alpha;";return`
  ${D.registerUniforms(Y).declareVariables(...V)}
  var<workgroup> tile_a: array<array<${L.type.storage}, ${b}>, ${b}>;
  var<workgroup> tile_b: array<array<${Z.type.storage}, ${b}>, ${b}>;
  ${D.mainStart([b,b,1])}
    let tile_col_start = (workgroup_index % uniforms.num_tile_n) * ${b};
    let tile_row_start = (workgroup_index / uniforms.num_tile_n) * ${b};
    let num_tiles = (uniforms.K - 1) / ${b} + 1;
    var k_start = 0u;
    var value = ${J.type.value}(0);
    for (var t: u32 = 0u; t < num_tiles; t++) {
      ${de}
      k_start = k_start + ${b};
      workgroupBarrier();

      for (var k: u32 = 0u; k < ${b}; k++) {
        ${se}
      }
      workgroupBarrier();
    }

    ${fe}
    let m = tile_row_start + local_id.y;
    let n = tile_col_start + local_id.x;
    ${W!=null?`let cOffset = ${W.broadcastedIndicesToOffset("vec2(m, n)",J)}; value += ${J.type.value}(uniforms.beta) * ${W.getByOffset("cOffset")};`:""}
    if (m < uniforms.M && n < uniforms.N) {
      output[m * uniforms.N + n] = value;
    }
  }`};return{name:"GemmShared",shaderCache:{hint:`${i.cacheKey}`,inputDependencies:O},getRunData:()=>({outputs:[{dims:y,dataType:t[0].dataType}],dispatchGroup:{x:$*k},programUniforms:I}),getShaderSource:M}},oh=t=>{let i=t.transA,n=t.transB,u=t.alpha,l=t.beta;return{transA:i,transB:n,alpha:u,beta:l,cacheKey:`${t.transA};${t.transB};${t.alpha===1}`}},uh=(t,i)=>{nh(t.inputs),t.compute(sh(t.inputs,i))}}),Ki,ya,En,In,lh,dh,ph,ch,hh,fh,mh,gh,yh,_h,Wv=m(()=>{it(),Xe(),j(),Ke(),[Ki,ya,En,In]=[0,1,2,3],lh=t=>{if(t[0].dims.length!==4)throw new Error("only 4-D tensor is supported.");if(t[0].dims.length!==t[1].dims.length)throw new Error("input dimensions must be equal to grid dimensions");if(t[0].dims.length-2!==t[1].dims[t[1].dims.length-1])throw new Error(`last dimension of grid must be equal to ${t[0].dims.length-2}`);if(t[0].dims[0]!==t[1].dims[0])throw new Error("grid batch size must match input batch size")},dh=`
  fn gs_get_cubic_coeffs(x: f32) -> vec4<f32> {
    let cubic_alpha = -0.75f;
    let x_abs = abs(x);
    var coeffs: vec4<f32>;
    coeffs[0] = (((cubic_alpha * (x_abs + 1) - 5 * cubic_alpha) * (x_abs + 1) + 8 * cubic_alpha) * (x_abs + 1) - 4 * cubic_alpha);
    coeffs[1] = (((cubic_alpha + 2) * x_abs - (cubic_alpha + 3)) * x_abs * x_abs + 1);
    coeffs[2] = (((cubic_alpha + 2) * (1 - x_abs) - (cubic_alpha + 3)) * (1 - x_abs) * (1 - x_abs) + 1);
    coeffs[3] = (((cubic_alpha * (2 - x_abs) - 5 * cubic_alpha) * (2 - x_abs) + 8 * cubic_alpha) * (2 - x_abs) - 4 * cubic_alpha);
    return coeffs;
  }
`,ph=t=>`
  fn gs_bicubic_interpolate(p: mat4x4<${t}>, x: f32, y: f32) -> ${t} {
    var v: vec4<f32>;
    var coeffs = gs_get_cubic_coeffs(x);
    for (var i = 0; i < 4; i++) {
      v[i] = coeffs[0] * p[i][0] + coeffs[1] * p[i][1] + coeffs[2] * p[i][2] + coeffs[3] * p[i][3];
    }
    coeffs = gs_get_cubic_coeffs(y);
    let pixel = ${t}(coeffs[0] * v[0] + coeffs[1] * v[1] + coeffs[2] * v[2] + coeffs[3] * v[3]);
    return pixel;
  }
`,ch=t=>`
  fn gs_denormalize(n: f32, length: i32) -> f32 {
    ${t.alignCorners===0?`
    // alignCorners: false => [-1, 1] to [-0.5, length - 0.5]
    return ((n + 1.0) * f32(length) - 1.0) / 2.0;
    `:`
    // alignCorners: true => [-1, 1] to [0, length - 1]
    return (n + 1.0) / 2.0 * (f32(length - 1));
    `}
  }
`,hh=t=>`
  ${t.paddingMode==="reflection"?`
      fn gs_reflect(x: i32, x_min: f32, x_max: f32) -> u32 {
        var dx = 0.0;
        var fx = f32(x);
        let range = x_max - x_min;
        if (fx < x_min) {
          dx = x_min - fx;
          let n = u32(dx / range);
          let r = dx - f32(n) * range;
          if (n % 2 == 0) {
            fx = x_min + r;
          } else {
            fx = x_max - r;
          }
        } else if (fx > x_max) {
          dx = fx - x_max;
          let n = u32(dx / range);
          let r = dx - f32(n) * range;
          if (n % 2 == 0) {
            fx = x_max - r;
          } else {
            fx = x_min + r;
          }
        }
        return u32(fx);
      }`:""}
`,fh=(t,i,n)=>`
  fn pixel_at_grid(r: i32, c: i32, H: i32, W: i32, batch: u32, channel: u32, border: vec4<f32>) -> ${i} {
     var pixel = ${i}(0);
     var indices = vec4<u32>(0);
     indices[${Ki}] = batch;
     indices[${ya}] = channel;`+(()=>{switch(n.paddingMode){case"zeros":return`
          if (r >= 0 && r < H && c >=0 && c < W) {
            indices[${En}] = u32(r);
            indices[${In}] = u32(c);
          } else {
            return ${i}(0);
          }
        `;case"border":return`
          indices[${En}] = u32(clamp(r, 0, H - 1));
          indices[${In}] = u32(clamp(c, 0, W - 1));
        `;case"reflection":return`
          indices[${En}] = gs_reflect(r, border[1], border[3]);
          indices[${In}] = gs_reflect(c, border[0], border[2]);
        `;default:throw new Error(`padding mode ${n.paddingMode} is not supported`)}})()+`
    return ${t.getByIndices("indices")};
  }
`,mh=(t,i,n)=>(()=>{switch(n.mode){case"nearest":return`
          let result = pixel_at_grid(i32(round(y)), i32(round(x)), H_in, W_in, indices[${Ki}], indices[${ya}], border);
        `;case"bilinear":return`
          let x1 = i32(floor(x));
          let y1 = i32(floor(y));
          let x2 = x1 + 1;
          let y2 = y1 + 1;

          let p11 = pixel_at_grid(y1, x1, H_in, W_in, indices[${Ki}], indices[${ya}], border);
          let p12 = pixel_at_grid(y1, x2, H_in, W_in, indices[${Ki}], indices[${ya}], border);
          let p21 = pixel_at_grid(y2, x1, H_in, W_in, indices[${Ki}], indices[${ya}], border);
          let p22 = pixel_at_grid(y2, x2, H_in, W_in, indices[${Ki}], indices[${ya}], border);

          let dx2 = ${i}(f32(x2) - x);
          let dx1 = ${i}(x - f32(x1));
          let dy2 = ${i}(f32(y2) - y);
          let dy1 = ${i}(y - f32(y1));
          let result = dy2 * (dx2 * p11 + dx1 * p12) + dy1 * (dx2 * p21 + dx1 * p22);
        `;case"bicubic":return`
          let x0 = i32(floor(x)) - 1;
          let y0 = i32(floor(y)) - 1;
          var p: mat4x4<${i}>;
          for (var h = 0; h < 4; h++) {
            for (var w = 0; w < 4; w++) {
              p[h][w] = pixel_at_grid(h + y0, w + x0, H_in, W_in, indices[${Ki}], indices[${ya}], border);
            }
          }

          let dx = x - f32(x0 + 1);
          let dy = y - f32(y0 + 1);
          let result = gs_bicubic_interpolate(p, dx, dy);
        `;default:throw new Error(`mode ${n.mode} is not supported`)}})()+`${t.setByOffset("global_idx","result")}`,gh=(t,i)=>{let n=oe("x",t[0].dataType,t[0].dims.length),u=[t[1].dims[0],t[1].dims[1],t[1].dims[2]],l=oe("grid",t[1].dataType,u.length,2),c=[t[0].dims[0],t[0].dims[1],t[1].dims[1],t[1].dims[2]];i.format==="NHWC"&&(c=[t[0].dims[0],t[1].dims[1],t[1].dims[2],t[0].dims[3]],[Ki,ya,En,In]=[0,3,1,2]);let h=ke("output",t[0].dataType,c.length),y=n.type.value,b=he.size(c),$=[{type:12,data:b},...ie(t[0].dims,u,c)],k=I=>`
  ${I.registerUniform("output_size","u32").declareVariables(n,l,h)}
  ${dh}
  ${ph(y)}
  ${ch(i)}
  ${hh(i)}
  ${fh(n,y,i)}

  ${I.mainStart()}
    ${I.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
      let H_in = i32(uniforms.x_shape[${En}]);
      let W_in = i32(uniforms.x_shape[${In}]);

      ${i.alignCorners===0?`
      let x_min = -0.5;
      let x_max = f32(W_in) - 0.5;
      let y_min = -0.5;
      let y_max = f32(H_in) - 0.5;
      `:`
      let x_min = 0.0;
      let x_max = f32(W_in) - 1.0;
      let y_min = 0.0;
      let y_max = f32(H_in) - 1.0;
      `};
      let border = vec4<f32>(x_min, y_min, x_max, y_max);

      let indices = ${h.offsetToIndices("global_idx")};
      var grid_indices = vec3<u32>(indices[${Ki}], indices[${En}], indices[${In}]);
      let nxy = ${l.getByIndices("grid_indices")};
      var x = gs_denormalize(f32(nxy[0]), W_in);
      var y = gs_denormalize(f32(nxy[1]), H_in);

      ${mh(h,y,i)}
  }`;return{name:"GridSample",shaderCache:{hint:`${i.cacheKey}`,inputDependencies:["type","type"]},getRunData:I=>{let O=he.size(c);return{outputs:[{dims:c,dataType:I[0].dataType}],dispatchGroup:{x:Math.ceil(O/64)},programUniforms:$}},getShaderSource:k}},yh=(t,i)=>{lh(t.inputs),t.compute(gh(t.inputs,i))},_h=t=>N({alignCorners:t.align_corners,mode:t.mode,paddingMode:t.padding_mode,format:t.format})}),Lr,wh,bh,zl,$h,mo,vh,xh=m(()=>{it(),Xe(),j(),Hi(),ls(),Ke(),or(),Lr=(t,i)=>t.length>i&&t[i].dims.length>0?t[i]:void 0,wh=(t,i)=>{let n=t[0],u=Lr(t,1),l=Lr(t,2),c=Lr(t,3),h=Lr(t,4),y=Lr(t,5),b=Lr(t,6),$=Lr(t,7);if(n.dims.length!==3&&n.dims.length!==5)throw new Error("Input query is expected to have 3 or 5 dimensions");let k=n.dims[0],I=n.dims[1],O=n.dims.length===3?n.dims[2]:i.numHeads*n.dims[4],M=I,D=0,L=0,Z=Math.floor(O/i.numHeads);if(b&&$&&he.size(b.dims)&&he.size($.dims)){if(b.dims.length!==4)throw new Error('Input "past_key" is expected to have 4 dimensions');if(b.dims[0]!==k||b.dims[1]!==i.numHeads||b.dims[3]!==Z)throw new Error('Input "past_key" shape (batch_size, num_heads, past_sequence_length, head_size)');if($.dims[0]!==k||$.dims[1]!==i.numHeads||$.dims[3]!==Z)throw new Error('Input "past_value" shape (batch_size, num_heads, past_sequence_length, head_size)');if(b.dims[2]!==$.dims[2])throw new Error('Input "past_key" and "past_value" shall have same dim 2 (past_sequence_length)');if($.dims.length!==4)throw new Error('Input "past_value" is expected to have 4 dimensions');D=b.dims[2],L=b.dims[2]}else if(b&&he.size(b.dims)||$&&he.size($.dims))throw new Error('Input "past_key" and "past_value" shall be both present or both absent');let W;if(u&&he.size(u.dims)>0){if(n.dims.length!==3)throw new Error('Input "query" is expected to have 3 dimensions when key is given');if(u.dims.length<3||u.dims.length>5)throw new Error('Input "key" is expected to have 3, 4, or 5 dimensions');if(n.dims[0]!==u.dims[0])throw new Error('Input "query" and "key" shall have same dim 0 (batch size)');if(u.dims.length===3){if(u.dims[2]!==n.dims[2])throw new Error('Input "query" and "key" shall have same dim 2 (hidden_size)');W=2,M=u.dims[1]}else if(u.dims.length===5){if(u.dims[2]!==i.numHeads||u.dims[3]!==2||u.dims[4]!==Z)throw new Error('Expect "key" shape (batch_size, kv_sequence_length, num_heads, 2, head_size) for packed kv');if(l)throw new Error('Expect "value" be none when "key" has packed kv format.');W=5,M=u.dims[1]}else{if(u.dims[1]!==i.numHeads||u.dims[3]!==Z)throw new Error('Expect "key" shape (batch_size, num_heads, kv_sequence_length, head_size) for past_key');W=0,M=u.dims[2]}}else{if(n.dims.length!==5)throw new Error('Input "query" is expected to have 5 dimensions when key is empty');if(n.dims[2]!==i.numHeads||n.dims[3]!==3)throw new Error('Expect "query" shape (batch_size, kv_sequence_length, num_heads, 3, head_size) for packed kv');W=3}if(c&&he.size(c.dims)>0){if(c.dims.length!==1)throw new Error('Input "bias" is expected to have 1 dimension');if(u&&u.dims.length===5&&u.dims[3]===2)throw new Error("bias is not allowed for packed kv.")}let V=D+M,J=0;if(h&&he.size(h.dims)>0){J=8;let fe=h.dims;throw fe.length===1?fe[0]===k?J=1:fe[0]===3*k+2&&(J=3):fe.length===2&&fe[0]===k&&fe[1]===V&&(J=5),J===8?new Error('Input "key_padding_mask" shape shall be (batch_size) or (batch_size, total_sequence_length)'):new Error("Mask not supported")}let Y=!1,se=O;if(l&&he.size(l.dims)>0){if(l.dims.length!==3&&l.dims.length!==4)throw new Error('Input "value" is expected to have 3 or 4 dimensions');if(n.dims[0]!==l.dims[0])throw new Error('Input "query" and "value" shall have same dim 0 (batch_size)');if(l.dims.length===3){if(M!==l.dims[1])throw new Error('Input "key" and "value" shall have the same dim 1 (kv_sequence_length)');se=l.dims[2]}else{if(M!==l.dims[2])throw new Error('Input "key" and "value" shall have the same dim 2 (kv_sequence_length)');se=l.dims[1]*l.dims[3],Y=!0}}let de=!1;if(h&&he.size(h.dims)>0)throw new Error("Key padding mask is not supported");if(y&&he.size(y.dims)>0){if(y.dims.length!==4)throw new Error('Input "attention_bias" is expected to have 4 dimensions');if(y.dims[0]!==k||y.dims[1]!==i.numHeads||y.dims[2]!==I||y.dims[3]!==V)throw new Error('Expect "attention_bias" shape (batch_size, num_heads, sequence_length, total_sequence_length)')}return{batchSize:k,sequenceLength:I,pastSequenceLength:D,kvSequenceLength:M,totalSequenceLength:V,maxSequenceLength:L,inputHiddenSize:0,hiddenSize:O,vHiddenSize:se,headSize:Z,vHeadSize:Math.floor(se/i.numHeads),numHeads:i.numHeads,isUnidirectional:!1,pastPresentShareBuffer:!1,maskFilterValue:i.maskFilterValue,maskType:J,scale:i.scale,broadcastResPosBias:de,passPastInKv:Y,qkvFormat:W}},bh=t=>N({...t}),zl=N({perm:[0,2,1,3]}),$h=(t,i,n,u,l,c,h)=>{let y=[u,l,c],b=he.size(y),$=[{type:12,data:b},{type:12,data:h},{type:12,data:c}],k=I=>{let O=ke("qkv_with_bias",i.dataType,y),M=oe("qkv",i.dataType,y),D=oe("bias",n.dataType,y),L=[{name:"output_size",type:"u32"},{name:"bias_offset",type:"u32"},{name:"hidden_size",type:"u32"}];return`
  ${I.registerUniforms(L).declareVariables(M,D,O)}
  ${I.mainStart()}
    ${I.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let bias_offset_idx = (global_idx % uniforms.hidden_size) + uniforms.bias_offset;

    qkv_with_bias[global_idx] = qkv[global_idx] + bias[bias_offset_idx];
  }`};return t.compute({name:"MultiHeadAttentionAddBias",shaderCache:{inputDependencies:["type","type"]},getRunData:()=>({outputs:[{dims:y,dataType:i.dataType,gpuDataType:0}],dispatchGroup:{x:Math.ceil(b/64)},programUniforms:$}),getShaderSource:k},{inputs:[i,n],outputs:[-1]})[0]},mo=(t,i,n,u,l,c,h,y)=>{let b=c;if(h&&he.size(h.dims)>0){if(u===1)throw new Error("AddBiasReshape is not implemented. Please export your model with packed QKV or KV");return b=$h(t,c,h,i,u,n*l,y),b=b.reshape([i,u,n,l]),n===1||u===1?b:t.compute(cr(b,zl.perm),{inputs:[b],outputs:[-1]})[0]}else return c.dims.length===3&&(b=c.reshape([i,u,n,l])),n===1||u===1?b:t.compute(cr(b,zl.perm),{inputs:[b],outputs:[-1]})[0]},vh=(t,i)=>{let n=wh(t.inputs,i),u=t.inputs[0],l=Lr(t.inputs,1),c=Lr(t.inputs,2),h=Lr(t.inputs,3),y=Lr(t.inputs,4),b=Lr(t.inputs,5),$=Lr(t.inputs,6),k=Lr(t.inputs,7);if(u.dims.length===5)throw new Error("Packed QKV is not implemented");if((l==null?void 0:l.dims.length)===5)throw new Error("Packed KV is not implemented");let I=l&&c&&l.dims.length===4&&c.dims.length===4,O=mo(t,n.batchSize,n.numHeads,n.sequenceLength,n.headSize,u,h,0);if(I)return Za(t,O,l,c,y,void 0,$,k,b,n);if(!l||!c)throw new Error("key and value must be provided");let M=mo(t,n.batchSize,n.numHeads,n.kvSequenceLength,n.headSize,l,h,n.hiddenSize),D=mo(t,n.batchSize,n.numHeads,n.kvSequenceLength,n.vHeadSize,c,h,2*n.hiddenSize);Za(t,O,M,D,y,void 0,$,k,b,n)}}),Sh,Th,kh,Eh,Cl,Ih,zh,Ch=m(()=>{it(),Xe(),j(),Ke(),Sh=t=>{if(!t||t.length<1)throw new Error("too few inputs")},Th=(t,i)=>{let n=[],u=i.numOutputs;return t[1].dims[0]>0&&(t[1].getBigInt64Array().forEach(l=>n.push(Number(l))),u=n.length),N({numOutputs:u,axis:i.axis,splitSizes:n})},kh=t=>`
fn calculateOutputIndex(index: u32) -> u32 {
    for (var i: u32 = 0u; i < ${t}u; i += 1u ) {
    if (index < ${ce("uniforms.size_in_split_axis","i",t)}) {
        return i;
    }
    }
    return ${t}u;
}`,Eh=t=>{let i=t.length,n=[];for(let u=0;u<i;++u){let l=t[u].setByIndices("indices","input[global_idx]");i===1?n.push(l):u===0?n.push(`if (output_number == ${u}u) { ${l} }`):u===i-1?n.push(`else { ${l} }`):n.push(`else if (output_number == ${u}) { ${l} }`)}return`
      fn writeBufferData(output_number: u32, indices: ${t[0].type.indices}, global_idx: u32) {
        ${n.join(`
`)}
      }`},Cl=(t,i)=>{let n=t[0].dims,u=he.size(n),l=t[0].dataType,c=he.normalizeAxis(i.axis,n.length),h=new Array(i.numOutputs),y=oe("input",l,n.length),b=new Array(i.numOutputs),$=[],k=[],I=0,O=[{type:12,data:u}];for(let D=0;D<i.numOutputs;D++){I+=i.splitSizes[D],b[D]=I;let L=n.slice();L[c]=i.splitSizes[D],k.push(L),h[D]=ke(`output${D}`,l,L.length),$.push({dims:k[D],dataType:t[0].dataType})}O.push({type:12,data:b},...ie(n,...k));let M=D=>`
  ${D.registerUniform("input_size","u32").registerUniform("size_in_split_axis","u32",b.length).declareVariables(y,...h)}
  ${kh(b.length)}
  ${Eh(h)}

  ${D.mainStart()}
    ${D.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.input_size")}

    var indices = ${y.offsetToIndices("global_idx")};
    var index = ${y.indicesGet("indices",c)};
    let output_number = calculateOutputIndex(index);
    if (output_number != 0) {
      index -= ${ce("uniforms.size_in_split_axis","output_number - 1u",b.length)};
      ${y.indicesSet("indices",c,"index")};
    }
    writeBufferData(output_number, indices, global_idx);
  }`;return{name:"Split",shaderCache:{hint:i.cacheKey,inputDependencies:["rank"]},getShaderSource:M,getRunData:()=>({outputs:$,dispatchGroup:{x:Math.ceil(u/64)},programUniforms:O})}},Ih=(t,i)=>{Sh(t.inputs);let n=t.inputs.length===1?i:Th(t.inputs,i);t.compute(Cl(t.inputs,n),{inputs:[0]})},zh=t=>{let i=t.axis,n=t.splitSizes,u=t.numOutputs<0?n.length:t.numOutputs;if(u!==n.length)throw new Error("numOutputs and splitSizes length must be equal");return N({axis:i,numOutputs:u,splitSizes:n})}}),Ah,Bu,Oh,Rh=m(()=>{it(),Xe(),j(),Ke(),Ah=(t,i)=>{let[n,u,l,c]=t,{numHeads:h,rotaryEmbeddingDim:y}=i;if(n.dims.length!==3&&n.dims.length!==4)throw new Error(`Input 'x' is expected to have 3 or 4 dimensions, got ${n.dims.length}`);if(!he.areEqual(u.dims,[])&&!he.areEqual(u.dims,[1])&&u.dims.length!==2)throw new Error(`Input 'position_ids' is expected to have 0, 1, or 2 dimensions, got ${u.dims.length}`);if(l.dims.length!==2)throw new Error(`Input 'cos_cache' is expected to have 2 dimensions, got ${l.dims.length}`);if(c.dims.length!==2)throw new Error(`Input 'sin_cache' is expected to have 2 dimensions, got ${c.dims.length}`);if(!he.areEqual(l.dims,c.dims))throw new Error("Inputs 'cos_cache' and 'sin_cache' are expected to have the same shape");if(y>0&&h===0)throw new Error("num_heads must be provided if rotary_embedding_dim is specified");let b=n.dims[0],$=n.dims[n.dims.length-2],k=l.dims[0],I=he.sizeFromDimension(n.dims,1)/$,O=y===0?l.dims[1]*2:I/h;if(y>O)throw new Error("rotary_embedding_dim must be less than or equal to head_size");if(u.dims.length===2){if(b!==u.dims[0])throw new Error(`Input 'position_ids' dimension 0 should be of size batch_size, got ${u.dims[0]}`);if($!==u.dims[1])throw new Error(`Input 'position_ids' dimension 1 should be of size sequence_length, got ${u.dims[1]}`)}if($>k)throw new Error("Updating cos_cache and sin_cache in RotaryEmbedding is not currently supported");if(O/2!==l.dims[1]&&y/2!==l.dims[1])throw new Error(`Input 'cos_cache' dimension 1 should be same as head_size / 2 or rotary_embedding_dim / 2, got ${l.dims[1]}`)},Bu=(t,i)=>{let{interleaved:n,numHeads:u,rotaryEmbeddingDim:l,scale:c}=i,h=t[0].dims[0],y=he.sizeFromDimension(t[0].dims,1),b=t[0].dims[t[0].dims.length-2],$=y/b,k=t[2].dims[1],I=l===0?k*2:$/u,O=new Array(h,b,$/I,I-k),M=he.computeStrides(O),D=[{type:1,data:c},{type:12,data:O},{type:12,data:M},...t[0].dims.length===3?new Array({type:12,data:[y,$,I,1]}):[],...t[0].dims.length===4?new Array({type:12,data:[y,I,b*I,1]}):[],...ie(t[0].dims,t[1].dims,t[2].dims,t[3].dims,t[0].dims)],L=Z=>{let W=oe("input",t[0].dataType,t[0].dims.length),V=oe("position_ids",t[1].dataType,t[1].dims.length),J=oe("cos_cache",t[2].dataType,t[2].dims.length),Y=oe("sin_cache",t[3].dataType,t[3].dims.length),se=ke("output",t[0].dataType,t[0].dims.length);return Z.registerUniforms([{name:"scale",type:"f32"},{name:"global_shape",type:"u32",length:O.length},{name:"global_strides",type:"u32",length:M.length},{name:"input_output_strides",type:"u32",length:M.length}]),`
        ${Z.declareVariables(W,V,J,Y,se)}

        ${Z.mainStart(te)}
          let half_rotary_emb_dim = uniforms.${J.name}_shape[1];
          let bsnh = global_idx / uniforms.global_strides % uniforms.global_shape;
          let size = uniforms.global_shape[0] * uniforms.global_strides[0];
          ${Z.guardAgainstOutOfBoundsWorkgroupSizes("size")}

          if (bsnh[3] < half_rotary_emb_dim) {
            let position_ids_idx =
                ${V.broadcastedIndicesToOffset("bsnh.xy",ke("",V.type.tensor,2))};
            let position_id =
                u32(${V.getByOffset("position_ids_idx")}) + select(0, bsnh[1], position_ids_idx == 0);
            let i = dot(bsnh, uniforms.input_output_strides) + select(0, bsnh[3], ${n});
            let j = i + select(half_rotary_emb_dim, 1, ${n});
            let re = ${W.getByOffset("i")} * ${J.get("position_id","bsnh[3]")} -
                ${W.getByOffset("j")} * ${Y.get("position_id","bsnh[3]")};
            ${se.setByOffset("i","re")}
            let im = ${W.getByOffset("i")} * ${Y.get("position_id","bsnh[3]")} +
                ${W.getByOffset("j")} * ${J.get("position_id","bsnh[3]")};
            ${se.setByOffset("j","im")}
          } else {
            let k = dot(bsnh, uniforms.input_output_strides) + half_rotary_emb_dim;
            ${se.setByOffset("k",W.getByOffset("k"))}
          }
        }`};return{name:"RotaryEmbedding",shaderCache:{hint:N({interleaved:n}).cacheKey,inputDependencies:["rank","rank","rank","rank"]},getShaderSource:L,getRunData:()=>({outputs:[{dims:t[0].dims,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(he.size(O)/te)},programUniforms:D})}},Oh=(t,i)=>{Ah(t.inputs,i),t.compute(Bu(t.inputs,i))}}),Bh,Mh,Al,Dh,Nh,Gv=m(()=>{j(),it(),ls(),xh(),Ch(),or(),Rh(),Ke(),Bh=(t,i)=>{if(i.doRotary&&t.length<=7)throw new Error("cos_cache and sin_cache inputs are required if do_rotary is specified");let n=t[0],u=t[1],l=t[2],c=t[3],h=t[4];if(i.doRotary!==0&&t.length<=7)throw new Error("cos_cast and sin_cache are expected if do_rotary attribute is non-zero");if(i.localWindowSize!==-1)throw new Error("Local attention is not supported");if(i.softcap!==0)throw new Error("Softcap is not supported");if(i.rotaryInterleaved!==0)throw new Error("Rotary interleaved is not supported");if(i.smoothSoftmax)throw new Error("Smooth softmax is not supported");if(n.dims.length!==3&&n.dims.length!==5)throw new Error("Input query is expected to have 3 or 5 dimensions");let y=n.dims[0],b=n.dims[1],$=n.dims.length===3?n.dims[2]:i.numHeads*n.dims[4],k=b,I=0,O=!u||u.dims.length===0,M=Math.floor(O?$/(i.numHeads+2*i.kvNumHeads):$/i.numHeads);O&&($=M*i.numHeads);let D=c&&c.dims.length!==0,L=h&&h.dims.length!==0;if(D&&c.dims.length===4&&c.dims[0]===y&&c.dims[1]!==i.kvNumHeads&&c.dims[2]===i.kvNumHeads&&c.dims[3]===M)throw new Error("BSNH pastKey/pastValue is not supported");if(D&&L){if(c.dims.length!==4)throw new Error('Input "past_key" is expected to have 4 dimensions');if(h.dims.length!==4)throw new Error('Input "past_value" is expected to have 4 dimensions');I=c.dims[2]}else if(D||L)throw new Error('Input "past_key" and "past_value" shall be both present or both absent');let Z=1;if(u&&u.dims.length>0){if(n.dims.length!==3)throw new Error('Input "query" is expected to have 3 dimensions when key is given');if(u.dims.length<3||u.dims.length>5)throw new Error('Input "key" is expected to have 3, 4, or 5 dimensions');if(n.dims[0]!==u.dims[0])throw new Error('Input "query" and "key" shall have same dim 0 (batch size)');if(u.dims.length===3){if(n.dims[2]%u.dims[2]!==0)throw new Error('Dimension 2 of "query" should be a multiple of "key"');k=u.dims[1]}else if(u.dims.length===5){if(u.dims[2]!==i.numHeads||u.dims[3]!==2||u.dims[4]!==M)throw new Error('Expect "key" shape (batch_size, kv_sequence_length, num_heads, 2, head_size) for packed kv');if(l)throw new Error('Expect "value" be none when "key" has packed kv format.');k=u.dims[1]}else{if(u.dims[1]!==i.numHeads||u.dims[3]!==M)throw new Error('Expect "key" shape (batch_size, num_heads, kv_sequence_length, head_size) for past_key');k=u.dims[2]}}else{if(n.dims.length!==3&&n.dims.length!==5)throw new Error('Input "query" is expected to have 3 or 5 dimensions when key is empty');if(n.dims.length===5&&(n.dims[2]!==i.numHeads||n.dims[3]!==3))throw new Error('Expect "query" shape (batch_size, kv_sequence_length, num_heads, 3, head_size) for packed kv');Z=3}let W=0,V=!1,J=i.kvNumHeads?M*i.kvNumHeads:$;if(l&&l.dims.length>0){if(l.dims.length!==3&&l.dims.length!==4)throw new Error('Input "value" is expected to have 3 or 4 dimensions');if(n.dims[0]!==l.dims[0])throw new Error('Input "query" and "value" shall have same dim 0 (batch_size)');if(l.dims.length===3){if(k!==l.dims[1])throw new Error('Input "key" and "value" shall have the same dim 1 (kv_sequence_length)');J=l.dims[2]}else{if(k!==l.dims[2])throw new Error('Input "past_key" and "past_value" shall have the same dim 2 (kv_sequence_length)');J=l.dims[1]*l.dims[3],V=!0}}let Y=t.length>4?t[5]:void 0;if(Y){if(Y.dims.length===0)throw new Error("seqlens_k must be at least 1D, got scalar.");let se=Y.dims.reduce((de,fe)=>de*fe,1);if(se!==y)throw new Error(`seqlens_k must have batch_size (${y}) elements, got ${se}.`);for(let de=0;de<Y.dims.length;de++)if(Y.dims[de]!==1&&Y.dims[de]!==y)throw new Error(`seqlens_k has unexpected shape. Each dimension must be 1 or batch_size (${y}), got dims[${de}] = ${Y.dims[de]}.`)}return{batchSize:y,sequenceLength:b,pastSequenceLength:I,kvSequenceLength:k,totalSequenceLength:-1,maxSequenceLength:-1,inputHiddenSize:0,hiddenSize:$,vHiddenSize:J,headSize:M,vHeadSize:Math.floor(J/i.kvNumHeads),numHeads:i.numHeads,kvNumHeads:i.kvNumHeads,nReps:i.numHeads/i.kvNumHeads,pastPresentShareBuffer:!1,maskType:W,scale:i.scale,broadcastResPosBias:!1,passPastInKv:V,qkvFormat:Z}},Mh=N({perm:[0,2,1,3]}),Al=(t,i,n)=>{let u=i,l=n.kvNumHeads;return i.dims.length===3&&n.kvSequenceLength!==0&&(u=i.reshape([n.batchSize,n.kvSequenceLength,l,n.headSize]),u=t.compute(cr(u,Mh.perm),{inputs:[u],outputs:[-1]})[0]),u},Dh=(t,i,n,u)=>{let l=7,c=["type","type"],h=[t*i],y=t*i,b=[{type:12,data:y},{type:12,data:i},{type:12,data:t}],$=k=>{let I=oe("seq_lens",n.dataType,n.dims),O=oe("total_seq_lens",u.dataType,u.dims),M=ke("pos_ids",l,h),D=[{name:"output_size",type:"u32"},{name:"sequence_length",type:"u32"},{name:"batch_size",type:"u32"}];return`
  ${k.registerUniforms(D).declareVariables(I,O,M)}
  ${k.mainStart()}
    ${k.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let total_sequence_length = u32(${O.getByOffset("0")});
    let is_subsequent_prompt = uniforms.sequence_length > 1 && uniforms.sequence_length != total_sequence_length;
    let is_first_prompt = !is_subsequent_prompt && uniforms.sequence_length == total_sequence_length;
    let batch_idx = global_idx / uniforms.sequence_length;
    let sequence_idx = i32(global_idx % uniforms.sequence_length);
    var pos_id: i32 = 0;
    let seqlen = ${I.getByOffset("batch_idx")};
    let total_seqlen = seqlen + 1;
    if (is_first_prompt) {
      if (sequence_idx < total_seqlen) {
        pos_id = sequence_idx;
      } else {
        pos_id = 1;
      }
      ${M.setByOffset("global_idx","pos_id")}
    } else if (is_subsequent_prompt) {
      let past_seqlen = total_seqlen - i32(uniforms.sequence_length);
      if (past_seqlen + sequence_idx < total_seqlen) {
        pos_id = past_seqlen + sequence_idx;
      } else {
        pos_id = 1;
      }
      ${M.setByOffset("global_idx","pos_id")}
    } else if (global_idx < uniforms.batch_size) {
      ${M.setByOffset("global_idx","seqlen")}
    };
  }
  `};return{name:"GeneratePositionIds",shaderCache:{hint:`${t};${i}`,inputDependencies:c},getRunData:()=>({outputs:[{dims:h,dataType:l}],dispatchGroup:{x:Math.ceil(y/64)},programUniforms:b}),getShaderSource:$}},Nh=(t,i)=>{var Y;let n=Bh(t.inputs,i);if(t.inputs[0].dims.length===5)throw new Error("Packed QKV is not implemented");if(((Y=t.inputs[1])==null?void 0:Y.dims.length)===5)throw new Error("Packed KV is not implemented");let u=t.inputs[0],l=t.inputs[1]&&t.inputs[1].dims.length>0?t.inputs[1]:void 0,c=t.inputs[2]&&t.inputs[2].dims.length>0?t.inputs[2]:void 0,h=t.inputs[3]&&t.inputs[3].dims.length!==0?t.inputs[3]:void 0,y=t.inputs[4]&&t.inputs[4].dims.length!==0?t.inputs[4]:void 0,b=t.inputs.length>4?t.inputs[5]:void 0,$=t.inputs.length>5?t.inputs[6]:void 0,k=n.kvNumHeads?n.kvNumHeads:n.numHeads,I=N({axis:2,numOutputs:3,splitSizes:[n.numHeads*n.headSize,k*n.headSize,k*n.headSize]}),[O,M,D]=!l&&!c?t.compute(Cl([u],I),{inputs:[u],outputs:[-1,-1,-1]}):[u,l,c],L,Z;if(i.doRotary){let se=t.compute(Dh(n.batchSize,n.sequenceLength,b,$),{inputs:[b,$],outputs:[-1]})[0],de=t.inputs[7],fe=t.inputs[8],we=N({interleaved:i.rotaryInterleaved!==0,numHeads:n.numHeads,rotaryEmbeddingDim:0,scale:i.scale}),xe=[O,se,de,fe],De=[-1];L=t.compute(Bu(xe,we),{inputs:xe,outputs:De})[0],xe.splice(0,1,M);let at=N({interleaved:i.rotaryInterleaved!==0,numHeads:n.kvNumHeads,rotaryEmbeddingDim:0,scale:i.scale});Z=t.compute(Bu(xe,at),{inputs:xe,outputs:De})[0]}let W=mo(t,n.batchSize,n.numHeads,n.sequenceLength,n.headSize,i.doRotary?L:O,void 0,0),V=Al(t,i.doRotary?Z:M,n),J=Al(t,D,n);Za(t,W,V,J,void 0,void 0,h,y,void 0,n,b,$)}}),Ol,Ph,Uh,Lh,Fv=m(()=>{it(),Xe(),or(),Ke(),Ol=(t,i,n,u,l,c,h,y)=>{let b=le(c),$=b===1?"f32":`vec${b}f`,k=b===1?"vec2f":`mat2x${b}f`,I=l*h,O=64;I===1&&(O=256);let M=[l,h,c/b],D=[l,h,2],L=["rank","type","type"],Z=[];Z.push(...ie(M,D));let W=V=>{let J=oe("x",i.dataType,3,b),Y=oe("scale",n.dataType,n.dims),se=oe("bias",u.dataType,u.dims),de=ke("output",1,3,2),fe=[J,Y,se,de];return`
  var<workgroup> workgroup_shared : array<${k}, ${O}>;
  const workgroup_size = ${O}u;
  ${V.declareVariables(...fe)}
  ${V.mainStart(O)}
    let batch = workgroup_index / uniforms.x_shape[1];
    let channel = workgroup_index % uniforms.x_shape[1];
    let hight = uniforms.x_shape[2];
    // initialize workgroup memory
    var sum = ${$}(0);
    var squared_sum = ${$}(0);
    for (var h = local_idx; h < hight; h += workgroup_size) {
      let value = ${$}(${J.get("batch","channel","h")});
      sum += value;
      squared_sum += value * value;
    }
    workgroup_shared[local_idx] = ${k}(sum, squared_sum);
    workgroupBarrier();

    for (var currSize = workgroup_size >> 1;  currSize > 0; currSize = currSize >> 1) {
      if (local_idx < currSize) {
        workgroup_shared[local_idx] = workgroup_shared[local_idx] + workgroup_shared[local_idx + currSize];
      }
      workgroupBarrier();
    }
    if (local_idx == 0) {
      let sum_final = ${be("workgroup_shared[0][0]",b)} / f32(hight * ${b});
      let squared_sum_final = ${be("workgroup_shared[0][1]",b)} / f32(hight * ${b});

      let inv_std_dev = inverseSqrt(squared_sum_final - sum_final * sum_final + f32(${y}));
      let channel_scale = inv_std_dev * f32(scale[channel]);
      let channel_shift = f32(bias[channel]) - sum_final * channel_scale;
      output[workgroup_index] = vec2f(channel_scale, channel_shift);
    }
  }`};return t.compute({name:"InstanceNormComputeChannelScaleShift",shaderCache:{hint:`${b};${y};${O}`,inputDependencies:L},getRunData:()=>({outputs:[{dims:D,dataType:1}],dispatchGroup:{x:I},programUniforms:Z}),getShaderSource:W},{inputs:[i,n,u],outputs:[-1]})[0]},Ph=(t,i,n)=>{let u=i[0].dims,l=u,c=2,h=u[0],y=u[1],b=he.sizeFromDimension(u,c),$=le(b),k=he.size(l)/$,I=Ol(t,i[0],i[1],i[2],h,b,y,n.epsilon),O=[h,y,b/$],M=[h,y],D=["type","none"],L=Z=>{let W=oe("x",i[0].dataType,O.length,$),V=oe("scale_shift",1,M.length,2),J=ke("output",i[0].dataType,O.length,$),Y=[W,V,J];return`
  ${Z.registerUniform("output_size","u32").declareVariables(...Y)}
  ${Z.mainStart()}
  ${Z.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
      let outputIndices = ${J.offsetToIndices("global_idx")};
      let batch = outputIndices[0];
      let channel = outputIndices[1];
      let scale_shift = ${V.getByIndices("vec2<u32>(batch, channel)")};
      let value = ${W.getByOffset("global_idx")} * ${J.type.value}(scale_shift.x) + ${J.type.value}(scale_shift.y);
      ${J.setByOffset("global_idx","value")};
  }`};t.compute({name:"InstanceNormalization",shaderCache:{hint:`${$}`,inputDependencies:D},getRunData:()=>({outputs:[{dims:l,dataType:i[0].dataType}],dispatchGroup:{x:Math.ceil(k/64)},programUniforms:[{type:12,data:k},...ie(O,M,O)]}),getShaderSource:L},{inputs:[i[0],I]})},Uh=(t,i,n)=>{let u=i[0].dims,l=u,c=u[0],h=u[u.length-1],y=he.sizeFromDimension(u,1)/h,b=le(h),$=he.size(l)/b,k=[{type:12,data:y},{type:12,data:Math.floor(h/b)}],I=["type","type"],O=!1,M=[0,u.length-1];for(let W=0;W<u.length-2;W++)O=O||u[W+1]!==1,M.push(W+1);O=O&&u[u.length-1]!==1;let D=O?t.compute(cr(t.inputs[0],M),{inputs:[t.inputs[0]],outputs:[-1]})[0]:t.inputs[0].reshape(Array.from({length:u.length},(W,V)=>u[M[V]])),L=Ol(t,D,i[1],i[2],c,y,h,n.epsilon),Z=W=>{let V=ue(i[0].dataType),J=b===1?"vec2f":`mat${b}x2f`,Y=fe=>{let we=fe===0?"x":"y",xe=b===1?"f32":`vec${b}f`;switch(b){case 1:return`${V}(${xe}(scale.${we}))`;case 2:return`vec2<${V}>(${xe}(scale[0].${we}, scale[1].${we}))`;case 4:return`vec4<${V}>(${xe}(scale[0].${we}, scale[1].${we}, scale[2].${we}, scale[3].${we}))`;default:throw new Error(`Not supported compoents ${b}`)}},se=oe("input",i[0].dataType,i[0].dims,b),de=ke("output",i[0].dataType,l,b);return`
  @group(0) @binding(0) var<storage, read> input : array<${se.type.storage}>;
  @group(0) @binding(1) var<storage, read> scale_input : array<${J}>;
  @group(0) @binding(2) var<storage, read_write> output : array<${de.type.storage}>;
  struct Uniforms {H: u32, C : u32};
  @group(0) @binding(3) var<uniform> uniforms: Uniforms;

  ${W.mainStart()}
    let current_image_number = global_idx / (uniforms.C * uniforms.H);
    let current_channel_number = global_idx % uniforms.C;

    let scale_offset = current_image_number * uniforms.C + current_channel_number;
    let scale = scale_input[scale_offset];
    output[global_idx] = fma(input[global_idx], ${Y(0)}, ${Y(1)});
  }`};t.compute({name:"InstanceNormalizationNHWC",shaderCache:{hint:`${b}`,inputDependencies:I},getRunData:()=>({outputs:[{dims:l,dataType:i[0].dataType}],dispatchGroup:{x:Math.ceil($/64)},programUniforms:k}),getShaderSource:Z},{inputs:[i[0],L]})},Lh=(t,i)=>{i.format==="NHWC"?Uh(t,t.inputs,i):Ph(t,t.inputs,i)}}),qh,Vh,Wh,Hv=m(()=>{it(),Xe(),Ke(),qh=t=>{if(!t||t.length<2)throw new Error("layerNorm requires at least 2 inputs.")},Vh=(t,i,n)=>{let u=i.simplified,l=t[0].dims,c=t[1],h=!u&&t[2],y=l,b=he.normalizeAxis(i.axis,l.length),$=he.sizeToDimension(l,b),k=he.sizeFromDimension(l,b),I=he.size(c.dims),O=h?he.size(h.dims):0;if(I!==k||h&&O!==k)throw new Error(`Size of X.shape()[axis:] == ${k}.
       Size of scale and bias (if provided) must match this.
       Got scale size of ${I} and bias size of ${O}`);let M=[];for(let se=0;se<l.length;++se)se<b?M.push(l[se]):M.push(1);let D=le(k),L=["type","type"],Z=[{type:12,data:$},{type:1,data:k},{type:12,data:Math.floor(k/D)},{type:1,data:i.epsilon}];h&&L.push("type");let W=n>1,V=n>2,J=se=>{let de=ue(t[0].dataType),fe=[oe("x",t[0].dataType,t[0].dims,D),oe("scale",c.dataType,c.dims,D)];h&&fe.push(oe("bias",h.dataType,h.dims,D)),fe.push(ke("output",t[0].dataType,y,D)),W&&fe.push(ke("mean_data_output",1,M)),V&&fe.push(ke("inv_std_output",1,M));let we=[{name:"norm_count",type:"u32"},{name:"norm_size",type:"f32"},{name:"norm_size_vectorized",type:"u32"},{name:"epsilon",type:"f32"}];return`
  ${se.registerUniforms(we).declareVariables(...fe)}
  ${se.mainStart()}
    ${se.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.norm_count")}
    let offset = global_idx * uniforms.norm_size_vectorized;
    var mean_vector = ${ve("f32",D)};
    var mean_square_vector = ${ve("f32",D)};

    for (var h: u32 = 0u; h < uniforms.norm_size_vectorized; h++) {
      let value = ${Se(de,D,"x[h + offset]")};
      mean_vector += value;
      mean_square_vector += value * value;
    }
    let mean = ${be("mean_vector",D)} / uniforms.norm_size;
    let inv_std_dev = inverseSqrt(${be("mean_square_vector",D)} / uniforms.norm_size ${u?"":"- mean * mean"} + uniforms.epsilon);

    for (var j: u32 = 0; j < uniforms.norm_size_vectorized; j++) {
      let f32input = ${Se(de,D,"x[j + offset]")};
      let f32scale = ${Se(de,D,"scale[j]")};
      output[j + offset] = ${fe[0].type.value}((f32input ${u?"":"- mean"}) * inv_std_dev * f32scale
        ${h?`+ ${Se(de,D,"bias[j]")}`:""}
      );
    }

    ${W?"mean_data_output[global_idx] = mean":""};
    ${V?"inv_std_output[global_idx] = inv_std_dev":""};
  }`},Y=[{dims:y,dataType:t[0].dataType}];return W&&Y.push({dims:M,dataType:1}),V&&Y.push({dims:M,dataType:1}),{name:"LayerNormalization",shaderCache:{hint:`${D};${n};${u}`,inputDependencies:L},getRunData:()=>({outputs:Y,dispatchGroup:{x:Math.ceil($/64)},programUniforms:Z}),getShaderSource:J}},Wh=(t,i)=>{qh(t.inputs),t.compute(Vh(t.inputs,i,t.outputCount))}}),Gh,Fh,jv=m(()=>{Xe(),fl(),_l(),Gh=t=>{if(!t||t.length!==2)throw new Error("MatMul requires 2 inputs.");if(t[0].dims[t[0].dims.length-1]!==t[1].dims[t[1].dims.length-2])throw new Error("shared dimension does not match.")},Fh=t=>{Gh(t.inputs);let i=hi.calcShape(t.inputs[0].dims,t.inputs[1].dims,!0);if(!i)throw new Error("Can't use matmul on the given tensors");let n=i[i.length-1],u=t.inputs[0].dims[t.inputs[0].dims.length-1];if(n<8&&u<8)t.compute(hl(t.inputs,{activation:""},i));else{let l=i[i.length-2],c=he.size(t.inputs[0].dims.slice(0,-2)),h=he.size(t.inputs[1].dims.slice(0,-2));if(c!==1&&l===1&&h===1){let y=t.inputs[0].reshape([1,c,u]),b=t.inputs[1].reshape([1,u,n]),$=[1,c,n],k=[y,b];t.compute(Cu(k,{activation:""},i,$),{inputs:k})}else t.compute(Cu(t.inputs,{activation:""},i))}}}),Hh,jh,Kh,Zh,Qh,Kv=m(()=>{it(),Xe(),j(),Ke(),Hh=(t,i)=>{if(t.length<3||t.length>4)throw new Error("MatMulNBits requires 3 or 4 inputs");let n=t[0],u=n.dims.length;if(n.dims[u-1]!==i.k)throw new Error("The last dim of input shape does not match the k value");let l=Math.floor((i.k+i.blockSize-1)/i.blockSize),c=i.blockSize/8*i.bits,h=t[1];if(!he.areEqual(h.dims,[i.n,l,c]))throw new Error("The second inputs must be 3D tensor with shape N X nBlocksPerCol X blobSize");let y=t[2].dims;if(he.size(y)!==i.n*l)throw new Error("scales input size error.");if(t.length===4){let b=t[3].dims,$=i.n*(i.bits===8?l:Math.floor((l*i.bits+7)/8));if(he.size(b)!==$)throw new Error("zeroPoints input size error.")}},jh=(t,i)=>{let n=t[0].dims,u=n.length,l=n[u-2],c=i.k,h=i.n,y=n.slice(0,u-2),b=he.size(y),$=t[1].dims[2]/4,k=t[0].dataType,I=le(i.k),O=le($),M=le(h),D=y.concat([l,h]),L=l>1&&h/M%2===0?2:1,Z=he.size(D)/M/L,W=64,V=[],J=[b,l,c/I],Y=he.convertShape(t[1].dims).slice();Y.splice(-1,1,$/O),V.push(...ie(J)),V.push(...ie(Y)),V.push(...ie(t[2].dims)),t.length===4&&V.push(...ie(he.convertShape(t[3].dims)));let se=[b,l,h/M];V.push(...ie(se));let de=fe=>{let we=J.length,xe=oe("a",t[0].dataType,we,I),De=oe("b",12,Y.length,O),at=oe("scales",t[2].dataType,t[2].dims.length),et=[xe,De,at],tt=t.length===4?oe("zero_points",12,t[3].dims.length):void 0;tt&&et.push(tt);let xt=se.length,zt=ke("output",t[0].dataType,xt,M),rt=ue(t[0].dataType),ot=(()=>{switch(I){case 1:return`array<${rt}, 8>`;case 2:return`mat4x2<${rt}>`;case 4:return`mat2x4<${rt}>`;default:throw new Error(`${I}-component is not supported.`)}})(),ur=Math.floor(32/i.bits),Ne=Math.floor(ur/8),Ot=()=>{let Ze="";for(let Pe=0;Pe<Ne;Pe++){let Vt=Pe*i.bits*4,_a=Vt+i.bits;Ze+=`
          // reuse a data (pass ${Pe})
            var input_offset${Pe>0?Pe:""} = ${Pe===0?xe.indicesToOffset(`${xe.type.indices}(batch, row, word_offset)`):"input_offset"};
            var a_data${Pe>0?Pe:""}: ${ot};
            for (var j${Pe>0?Pe:""}: u32 = 0; j${Pe>0?Pe:""} < ${8/I}; j${Pe>0?Pe:""}++) {
              a_data${Pe>0?Pe:""}[j${Pe>0?Pe:""}] = ${xe.getByOffset(`input_offset${Pe>0?Pe:""}`)};
              input_offset${Pe>0?Pe:""}++;
            }
          `;for(let Ir=0;Ir<M*L;Ir++)Ze+=`
            b_value = ${O===1?`b${Ir}_data`:`b${Ir}_data[i]`};
            ${i.bits===2?`{
              let half_word = b_value >> ${Pe*16}u;
              let byte_lo = half_word & 0xFFu;
              let byte_hi = (half_word >> 8u) & 0xFFu;
              let spread_word = (byte_lo & 0xFu) | ((byte_lo >> 4u) << 8u) | ((byte_hi & 0xFu) << 16u) | ((byte_hi >> 4u) << 24u);
              b_value_lower = unpack4xU8(spread_word & b_mask);
              b_value_upper = unpack4xU8((spread_word >> 2u) & b_mask);
            }`:`b_value_lower = unpack4xU8((b_value >> ${Vt}u) & b_mask);
            b_value_upper = unpack4xU8((b_value >> ${_a}u) & b_mask);`}
            b_quantized_values = ${ot}(${Array.from({length:4},(zi,Ci)=>`${rt}(b_value_lower[${Ci}]), ${rt}(b_value_upper[${Ci}])`).join(", ")});
            b_dequantized_values = ${I===1?`${ot}(${Array.from({length:8},(zi,Ci)=>`(b_quantized_values[${Ci}] - ${tt?`zero_point${Ir}`:"zero_point"}) * scale${Ir}`).join(", ")});`:`(b_quantized_values - ${ot}(${Array(8).fill(`${tt?`zero_point${Ir}`:"zero_point"}`).join(",")})) * scale${Ir};`};
            workgroup_shared[local_id.x * ${L} + ${Math.floor(Ir/M)}]${M>1?`[${Ir%M}]`:""} += ${Array.from({length:8/I},(zi,Ci)=>`${I===1?`a_data${Pe>0?Pe:""}[${Ci}] * b_dequantized_values[${Ci}]`:`dot(a_data${Pe>0?Pe:""}[${Ci}], b_dequantized_values[${Ci}])`}`).join(" + ")};
          `}return Ze},Ae=()=>{let Ze=`
            var col_index = col * ${M};
            ${tt?`
            let zero_point_values_per_byte: u32 = ${Math.floor(8/i.bits)}u;
            let zero_point_bytes_per_col = (nBlocksPerCol + zero_point_values_per_byte - 1u) / zero_point_values_per_byte;
            var zero_point_byte_count: u32;
            var zero_point_word_index: u32;
            var zero_point_byte_offset: u32;
            let zero_point_sub_offset: u32 = block % zero_point_values_per_byte;
            var zero_point_bits_offset: u32;
            var zero_point_word: u32;`:`
            // The default zero point is ${Math.pow(2,i.bits-1)} for unsigned ${i.bits}-bit quantization.
            let zero_point = ${rt}(${Math.pow(2,i.bits-1).toFixed(1)});`}
            `;for(let Pe=0;Pe<M*L;Pe++)Ze+=`
            let scale${Pe} = ${at.getByOffset("col_index * nBlocksPerCol + block")};
            ${tt?`
            zero_point_byte_count = col_index * zero_point_bytes_per_col + (block / zero_point_values_per_byte);
            zero_point_word_index = zero_point_byte_count >> 0x2u;
            zero_point_byte_offset = zero_point_byte_count & 0x3u;
            zero_point_bits_offset = (zero_point_byte_offset << 3) + (zero_point_sub_offset * ${i.bits}u);
            zero_point_word = ${tt.getByOffset("zero_point_word_index")} >> zero_point_bits_offset;
            let zero_point${Pe} = ${rt}((zero_point_word) & ${i.bits===2?"0x3u":"0xFu"});`:""}
            col_index += 1;`;return Ze},Ge=()=>{let Ze=`col_index = col * ${M};`;for(let Pe=0;Pe<M*L;Pe++)Ze+=`
            let b${Pe}_data = ${De.getByIndices(`${De.type.indices}(col_index, block, word)`)};
            col_index += 1;`;return Ze+=`
            var b_value: u32;
            let b_mask: u32 = ${i.bits===2?"0x03030303u":"0x0F0F0F0Fu"};
            var b_value_lower: vec4<u32>;
            var b_value_upper: vec4<u32>;
            var b_quantized_values: ${ot};
            var b_dequantized_values: ${ot};`,Ze};return`
        var<workgroup> workgroup_shared: array<${zt.type.value}, ${L*W}>;
        ${fe.declareVariables(...et,zt)}
        ${fe.mainStart([W,1,1])}
          let output_indices = ${zt.offsetToIndices(`(global_idx / ${W}) * ${L}`)};
          let col = output_indices[2];
          let row = output_indices[1];
          let batch = output_indices[0];
          let nBlocksPerCol = uniforms.b_shape[1];

          for (var block = local_id.x; block < nBlocksPerCol; block += ${W}) {
            //process one block
            var word_offset: u32 = block * ${i.blockSize/I};
            ${Ae()}
            for (var word: u32 = 0; word < ${$}; word += ${O}) {
              ${Ge()}
              for (var i: u32 = 0; i < ${O}; i++) {
                ${Ot()}
                word_offset += ${ur/I};
              }
            }
          }
          workgroupBarrier();

          if (local_id.x < ${L}) {
            var output_value: ${zt.type.value} = ${zt.type.value}(0);
            var workgroup_shared_offset: u32 = local_id.x;
            for (var b: u32 = 0u; b < ${W}u; b++) {
              output_value += workgroup_shared[workgroup_shared_offset];
              workgroup_shared_offset += ${L};
            }
            ${zt.setByIndices(`${zt.type.indices}(batch, row, col + local_id.x)`,"output_value")};
          }
        }`};return{name:"MatMulNBits",shaderCache:{hint:`${i.blockSize};${i.bits};${I};${O};${M};${L};${W}`,inputDependencies:Array(t.length).fill("rank")},getRunData:()=>({outputs:[{dims:D,dataType:k}],dispatchGroup:{x:Z},programUniforms:V}),getShaderSource:de}},Kh=(t,i)=>{let n=t[0].dims,u=n.length,l=n[u-2],c=i.k,h=i.n,y=n.slice(0,u-2),b=he.size(y),$=t[1].dims[2]/4,k=t[0].dataType,I=le(i.k),O=le($),M=y.concat([l,h]),D=128,L=h%8===0?8:h%4===0?4:1,Z=D/L,W=Math.floor(32/i.bits),V=Z*O*W,J=V/I,Y=V/i.blockSize,se=he.size(M)/L,de=[],fe=[b,l,c/I],we=he.convertShape(t[1].dims).slice();we.splice(-1,1,$/O),de.push(...ie(fe)),de.push(...ie(we)),de.push(...ie(t[2].dims)),t.length===4&&de.push(...ie(he.convertShape(t[3].dims)));let xe=[b,l,h];de.push(...ie(xe));let De=at=>{let et=fe.length,tt=oe("a",t[0].dataType,et,I),xt=oe("b",12,we.length,O),zt=oe("scales",t[2].dataType,t[2].dims.length),rt=[tt,xt,zt],ot=t.length===4?oe("zero_points",12,t[3].dims.length):void 0;ot&&rt.push(ot);let ur=xe.length,Ne=ke("output",t[0].dataType,ur),Ot=ue(t[0].dataType),Ae=()=>{switch(I){case 1:return`
          let a_data0 = vec4<${Ot}>(sub_a[word_offset], sub_a[word_offset + 1], sub_a[word_offset + 2], sub_a[word_offset + 3]);
          let a_data1 = vec4<${Ot}>(sub_a[word_offset + 4], sub_a[word_offset + 5], sub_a[word_offset + 6], sub_a[word_offset + 7]);`;case 2:return`
          let a_data0 = vec4<${Ot}>(sub_a[word_offset], sub_a[word_offset + 1]);
          let a_data1 = vec4<${Ot}>(sub_a[word_offset + 2], sub_a[word_offset + 3]);`;case 4:return`
          let a_data0 = sub_a[word_offset];
          let a_data1 = sub_a[word_offset + 1];`;default:throw new Error(`${I}-component is not supported.`)}};return`
        var<workgroup> sub_a: array<${tt.type.value}, ${J}>;
        var<workgroup> inter_results: array<array<${Ne.type.value}, ${Z}>, ${L}>;
        ${at.declareVariables(...rt,Ne)}
        ${at.mainStart([Z,L,1])}
          let output_indices = ${Ne.offsetToIndices(`workgroup_index * ${L}`)};
          let col = output_indices[2];
          let row = output_indices[1];
          let batch = output_indices[0];
          let n_blocks_per_col = uniforms.b_shape[1];
          let num_tiles =  (n_blocks_per_col - 1) / ${Y} + 1;

          // Loop over shared dimension.
          for (var tile: u32 = 0; tile < num_tiles; tile += 1) {
            let a_col_start = tile * ${J};
            // load one tile A data into shared memory.
            for (var a_offset = local_idx; a_offset < ${J}; a_offset += ${D})
            {
              let a_col = a_col_start + a_offset;
              if (a_col < uniforms.a_shape[2])
              {
                sub_a[a_offset] = ${tt.getByIndices(`${tt.type.indices}(batch, row, a_col)`)};
              } else {
                sub_a[a_offset] = ${tt.type.value}(0);
              }
            }
            workgroupBarrier();

            // each thread process one block
            let b_row = col + local_id.y;
            let block = tile * ${Y} + local_id.x;
            ${ot?`
            let zero_point_values_per_byte: u32 = ${Math.floor(8/i.bits)}u;
            let zero_point_bytes_per_col = (n_blocks_per_col + zero_point_values_per_byte - 1u) / zero_point_values_per_byte;
            let zero_point_byte_count = b_row * zero_point_bytes_per_col + (block / zero_point_values_per_byte);
            let zero_point_word_index = zero_point_byte_count >> 0x2u;
            let zero_point_byte_offset = zero_point_byte_count & 0x3u;
            let zero_point_sub_offset: u32 = block % zero_point_values_per_byte;
            let zero_point_bits_offset = (zero_point_byte_offset << 3) + (zero_point_sub_offset * ${i.bits}u);
            let zero_point_word = ${ot.getByOffset("zero_point_word_index")} >> zero_point_bits_offset;
            let zero_point = ${Ot}((zero_point_word) & ${i.bits===2?"0x3u":"0xFu"});`:`
            // The default zero point is ${Math.pow(2,i.bits-1)} for unsigned ${i.bits}-bit quantization.
            let zero_point = ${Ot}(${Math.pow(2,i.bits-1).toFixed(1)});`}
            let scale = ${zt.getByOffset("b_row * n_blocks_per_col + block")};
            let b_data = ${xt.getByIndices(`${xt.type.indices}(b_row, block, 0)`)};
            var word_offset = local_id.x * ${i.blockSize/I};
            for (var i: u32 = 0; i < ${O}; i++) {
              let b_value = ${O===1?"b_data":"b_data[i]"};
              ${(()=>{let Ge=Math.floor(W/8),Ze="";for(let Pe=0;Pe<Ge;Pe++){let Vt=Pe*i.bits*4,_a=Vt+i.bits;Ze+=`
              ${Ae()}
              {${i.bits===2?`
                let half_word = b_value >> ${Pe*16}u;
                let byte_lo = half_word & 0xFFu;
                let byte_hi = (half_word >> 8u) & 0xFFu;
                let spread_word = (byte_lo & 0xFu) | ((byte_lo >> 4u) << 8u) | ((byte_hi & 0xFu) << 16u) | ((byte_hi >> 4u) << 24u);
                let b_value_lower = unpack4xU8(spread_word & 0x03030303u);
                let b_value_upper = unpack4xU8((spread_word >> 2u) & 0x03030303u);`:`
                let b_value_lower = unpack4xU8((b_value >> ${Vt}u) & 0x0F0F0F0Fu);
                let b_value_upper = unpack4xU8((b_value >> ${_a}u) & 0x0F0F0F0Fu);`}
                let b_quantized_values = mat2x4<${Ot}>(${Array.from({length:4},(Ir,zi)=>`${Ot}(b_value_lower[${zi}]), ${Ot}(b_value_upper[${zi}])`).join(", ")});
                let b_dequantized_values = (b_quantized_values - mat2x4<${Ot}>(${Array(8).fill("zero_point").join(",")})) * scale;
                inter_results[local_id.y][local_id.x] += ${Array.from({length:2},(Ir,zi)=>`${`dot(a_data${zi}, b_dequantized_values[${zi}])`}`).join(" + ")};
              }
              word_offset += ${8/I};`}return Ze})()}
            }
            workgroupBarrier();
          }

          if (local_idx < ${L}) {
            var output_value: ${Ne.type.value} = ${Ne.type.value}(0);
            for (var b = 0u; b < ${Z}; b++) {
              output_value += inter_results[local_idx][b];
            }
            if (col + local_idx < uniforms.output_shape[2])
            {
              ${Ne.setByIndices(`${Ne.type.indices}(batch, row, col + local_idx)`,"output_value")}
            }
          }
        }`};return{name:"BlockwiseMatMulNBits32",shaderCache:{hint:`${i.blockSize};${I};${O};${Z};${L}`,inputDependencies:Array(t.length).fill("rank")},getRunData:()=>({outputs:[{dims:M,dataType:k}],dispatchGroup:{x:se},programUniforms:de}),getShaderSource:De}},Zh=(t,i)=>{Hh(t.inputs,i),i.blockSize===32&&t.adapterInfo.isVendor("intel")&&t.adapterInfo.isArchitecture("gen-12lp")?t.compute(Kh(t.inputs,i)):t.compute(jh(t.inputs,i))},Qh=t=>N(t)}),Xh,Yh,Jh,ef,tf,rf,af,nf,sf,Zv=m(()=>{it(),Xe(),Ke(),Xh=t=>{if(!t||t.length<1)throw new Error("Too few inputs");if(t[0].dataType!==1&&t[0].dataType!==10)throw new Error("Input type must be float or float16.");if(t.length>=2){let i=t[0].dims.length*2===t[1].dims[0];if(t.length===4&&(i=t[3].dims[0]*2===t[1].dims[0]),!i)throw new Error("The pads should be a 1D tensor of shape [2 * input_rank] or [2 * num_axes].")}},Yh=(t,i,n)=>{let u="";for(let l=i-1;l>=0;--l)u+=`
            k = i32(${t.indicesGet("indices",l)}) - ${ce("uniforms.pads",l,n)};
            if (k < 0) {
              break;
            }
            if (k >= i32(${ce("uniforms.x_shape",l,i)})) {
              break;
            }
            offset += k * i32(${ce("uniforms.x_strides",l,i)});
        `;return`
          value = ${t.type.value}(uniforms.constant_value);
          for (var i = 0; i < 1; i++) {
            var offset = 0;
            var k = 0;
            ${u}
            value = x[offset];
          }
      `},Jh=(t,i,n)=>{let u="";for(let l=i-1;l>=0;--l)u+=`
                k = i32(${t.indicesGet("indices",l)}) - ${ce("uniforms.pads",l,n)};
                if (k < 0) {
                  k = -k;
                }
                {
                  let _2n_1 = 2 * (i32(${ce("uniforms.x_shape",l,i)}) - 1);
                  k = k % _2n_1;
                  if(k >= i32(${ce("uniforms.x_shape",l,i)})) {
                    k = _2n_1 - k;
                  }
                }
                offset += k * i32(${ce("uniforms.x_strides",l,i)});
            `;return`
              var offset = 0;
              var k = 0;
              ${u}
              value = x[offset];
          `},ef=(t,i,n)=>{let u="";for(let l=i-1;l>=0;--l)u+=`
                k = i32(${t.indicesGet("indices",l)}) - ${ce("uniforms.pads",l,n)};
                if (k < 0) {
                  k = 0;
                }
                if (k >= i32(${ce("uniforms.x_shape",l,i)})) {
                  k = i32(${ce("uniforms.x_shape",l,i)}) - 1;
                }
                offset += k * i32(${ce("uniforms.x_strides",l,i)});
            `;return`
              var offset = 0;
              var k = 0;
              ${u}
              value = x[offset];
          `},tf=(t,i,n)=>{let u="";for(let l=i-1;l>=0;--l)u+=`
                k = i32(${t.indicesGet("indices",l)}) - ${ce("uniforms.pads",l,n)};
                if (k < 0)  {
                  k += i32(${ce("uniforms.x_shape",l,i)}]);
                }
                if (k >= i32(${ce("uniforms.x_shape",l,i)})) {
                  k -= i32(${ce("uniforms.x_shape",l,i)});
                }
                offset += k * i32(${ce("uniforms.x_strides",l,i)});
            `;return`
              var offset = 0;
              var k = 0;
              ${u}
              value = x[offset];
          `},rf=(t,i,n)=>{switch(n.mode){case 0:return Yh(t,i,n.pads.length);case 1:return Jh(t,i,n.pads.length);case 2:return ef(t,i,n.pads.length);case 3:return tf(t,i,n.pads.length);default:throw new Error("Invalid mode")}},af=(t,i)=>{let n=he.padShape(t[0].dims.slice(),i.pads),u=t[0].dims,l=he.size(n),c=[{type:12,data:l},{type:6,data:i.pads}],h=t.length>=3&&t[2].data;i.mode===0&&c.push({type:h?t[2].dataType:1,data:i.value}),c.push(...ie(t[0].dims,n));let y=["rank"],b=$=>{let k=ke("output",t[0].dataType,n.length),I=oe("x",t[0].dataType,u.length),O=I.type.value,M=rf(k,u.length,i),D=[{name:"output_size",type:"u32"},{name:"pads",type:"i32",length:i.pads.length}];return i.mode===0&&D.push({name:"constant_value",type:h?O:"f32"}),`
            ${$.registerUniforms(D).declareVariables(I,k)}
            ${$.mainStart()}
            ${$.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

            let indices = ${k.offsetToIndices("global_idx")};

            var value = ${O}(0);
            ${M}
            output[global_idx] = value;
        }`};return{name:"Pad",shaderCache:{hint:`${i.mode}${h}`,inputDependencies:y},getRunData:()=>({outputs:[{dims:n,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(he.size(n)/64)},programUniforms:c}),getShaderSource:b}},nf=(t,i)=>{if(t.length>1){let n=t[1].getBigInt64Array(),u=t.length>=3&&t[2].data?t[2].dataType===10?t[2].getUint16Array()[0]:t[2].getFloat32Array()[0]:0,l=t[0].dims.length,c=new Int32Array(2*l).fill(0);if(t.length>=4){let y=t[3].getBigInt64Array();for(let b=0;b<y.length;b++)c[Number(y[b])]=Number(n[b]),c[Number(y[b])+l]=Number(n[b+y.length])}else n.forEach((y,b)=>c[Number(b)]=Number(y));let h=[];return c.forEach(y=>h.push(y)),{mode:i.mode,value:u,pads:h}}else return i},sf=(t,i)=>{Xh(t.inputs);let n=nf(t.inputs,i);t.compute(af(t.inputs,n),{inputs:[0]})}}),go,Rl,Bl,Ml,Dl,of,uf,Nl,Pl,lf,df,Ul,pf,cf,Ll,hf,ff,mf,gf,Qv=m(()=>{Kt(),it(),Xe(),Ke(),go=t=>{if(B.webgpu.validateInputContent&&(!t||t.length!==1))throw new Error("Pool ops requires 1 input.")},Rl=(t,i,n)=>{let u=i.format==="NHWC",l=t.dims.slice();u&&l.splice(1,0,l.pop());let c=Object.hasOwnProperty.call(i,"dilations"),h=i.kernelShape.slice(),y=i.strides.slice(),b=c?i.dilations.slice():[],$=i.pads.slice();Fi.adjustPoolAttributes(n,l,h,y,b,$);let k=Fi.computePoolOutputShape(n,l,y,b,h,$,i.autoPad),I=Object.assign({},i);c?Object.assign(I,{kernelShape:h,strides:y,pads:$,dilations:b,cacheKey:i.cacheKey}):Object.assign(I,{kernelShape:h,strides:y,pads:$,cacheKey:i.cacheKey});let O=k.slice();return O.push(O.splice(1,1)[0]),[I,u?O:k]},Bl=(t,i)=>{let n=i.format==="NHWC",u=he.size(t),l=he.size(i.kernelShape),c=[{type:12,data:u},{type:12,data:l}],h=[{name:"outputSize",type:"u32"},{name:"kernelSize",type:"u32"}];if(i.kernelShape.length<=2){let y=i.kernelShape[i.kernelShape.length-1],b=i.strides[i.strides.length-1],$=i.pads[i.pads.length/2-1],k=i.pads[i.pads.length-1],I=!!($+k);c.push({type:12,data:y},{type:12,data:b},{type:12,data:$},{type:12,data:k}),h.push({name:"kw",type:"u32"},{name:"sw",type:"u32"},{name:"pwStart",type:"u32"},{name:"pwEnd",type:"u32"});let O=!1;if(i.kernelShape.length===2){let M=i.kernelShape[i.kernelShape.length-2],D=i.strides[i.strides.length-2],L=i.pads[i.pads.length/2-2],Z=i.pads[i.pads.length-2];O=!!(L+Z),c.push({type:12,data:M},{type:12,data:D},{type:12,data:L},{type:12,data:Z}),h.push({name:"kh",type:"u32"},{name:"sh",type:"u32"},{name:"phStart",type:"u32"},{name:"phEnd",type:"u32"})}return[c,h,!0,I,O]}else{if(n)throw new Error("Pooling with kernelShape.length > 2 is not supported for NHWC format.");let y=he.computeStrides(i.kernelShape);c.push({type:12,data:y},{type:12,data:i.pads},{type:12,data:i.strides}),h.push({name:"kernelStrides",type:"u32",length:y.length},{name:"pads",type:"u32",length:i.pads.length},{name:"strides",type:"u32",length:i.strides.length});let b=i.pads.reduce(($,k)=>$+k);return[c,h,!!b,!1,!1]}},Ml=(t,i,n,u,l,c,h,y,b,$,k,I)=>{let O=l.format==="NHWC",M=i.type.value,D=ke("output",i.type.tensor,u);if(l.kernelShape.length<=2){let L="",Z="",W="",V=n-(O?2:1);if(k?L=`
                for (var i: u32 = 0u; i < uniforms.kw; i++) {
                  xIndices[${V}] = indices[${V}] * uniforms.sw - uniforms.pwStart + i;
                  if (xIndices[${V}] < 0 || xIndices[${V}]
                      >= uniforms.x_shape[${V}]) {
                    pad++;
                    continue;
                  }
                  let x_val = x[${i.indicesToOffset("xIndices")}];
                  ${c}
                }`:L=`
                for (var i: u32 = 0u; i < uniforms.kw; i++) {
                  xIndices[${V}] = indices[${V}] * uniforms.sw - uniforms.pwStart + i;
                  let x_val = x[${i.indicesToOffset("xIndices")}];
                  ${c}
                }`,l.kernelShape.length===2){let J=n-(O?3:2);I?Z=`
                for (var j: u32 = 0u; j < uniforms.kh; j++) {
                  xIndices[${J}] = indices[${J}] * uniforms.sh - uniforms.phStart + j;
                  if (xIndices[${J}] < 0 || xIndices[${J}] >= uniforms.x_shape[${J}]) {
                    pad += i32(uniforms.kw);
                    continue;
                  }
              `:Z=`
                for (var j: u32 = 0u; j < uniforms.kh; j++) {
                  xIndices[${J}] = indices[${J}] * uniforms.sh - uniforms.phStart + j;
                `,W=`
              }
            `}return`
            ${t.registerUniforms(b).declareVariables(i,D)}

            ${t.mainStart()}
              ${t.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}

              let indices = ${D.offsetToIndices("global_idx")};
              var xIndices = ${D.offsetToIndices("global_idx")};

              var value = ${M}(${y});
              var pad = 0;
              ${Z}
              ${L}
              ${W}
              ${h}

              output[global_idx] = value;
            }`}else{if(O)throw new Error("Pooling with kernelShape.length > 2 is not supported for NHWC format.");let L=l.kernelShape.length,Z=l.pads.length,W="";return $?W=`
                if (xIndices[j] >= uniforms.x_shape[j]) {
                  pad++;
                  isPad = true;
                  break;
                }
              }
              if (!isPad) {
                let x_val = x[${i.indicesToOffset("xIndices")}];
                ${c}
              }`:W=`
              }
              let x_val = x[${i.indicesToOffset("xIndices")}];
              ${c}
            `,`
            ${t.registerUniforms(b).declareVariables(i,D)}

            ${t.mainStart()}
              ${t.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
              let indices = ${D.offsetToIndices("global_idx")};
              var xIndices = ${D.offsetToIndices("global_idx")};

              var offsets: array<u32, ${L}>;

              var value = ${M}(${y});
              var pad = 0;
              var isPad = false;

              for (var i: u32 = 0u; i < uniforms.kernelSize; i++) {
                var offset = i;
                for (var j = 0u; j < ${L-1}u; j++) {
                  offsets[j] = offset / ${ce("uniforms.kernelStrides","j",L)};
                  offset -= offsets[j] * ${ce("uniforms.kernelStrides","j",L)};
                }
                offsets[${L-1}] = offset;

                isPad = false;
                for (var j = ${n-L}u; j < ${n}u; j++) {
                  xIndices[j] = indices[j] * ${ce("uniforms.strides",`j - ${n-L}u`,L)}
                    + offsets[j - ${n-L}u] - ${ce("uniforms.pads","j - 2u",Z)};
                  ${W}
              }
              ${h}

              output[global_idx] = value;
            }`}},Dl=t=>`${t.format};${t.ceilMode};${t.autoPad};${t.kernelShape.length}`,of=t=>`${Dl(t)};${t.countIncludePad}`,uf=t=>`${Dl(t)};${t.storageOrder};${t.dilations}`,Nl=t=>({format:t.format,autoPad:["NOTSET","VALID","SAME_UPPER","SAME_LOWER"][t.auto_pad],ceilMode:t.ceil_mode,kernelShape:t.kernel_shape,strides:t.strides,pads:t.pads}),Pl=(t,i,n,u)=>{let[l,c]=Rl(i,u,n),h=oe("x",i.dataType,i.dims.length),y=h.type.value,b="value += x_val;",$="";l.countIncludePad?$+=`value /= ${y}(uniforms.kernelSize);`:$+=`value /= ${y}(i32(uniforms.kernelSize) - pad);`;let[k,I,O,M,D]=Bl(c,l);k.push(...ie(i.dims,c));let L=["rank"];return{name:t,shaderCache:{hint:`${u.cacheKey};${O};${M};${D}`,inputDependencies:L},getRunData:()=>({outputs:[{dims:c,dataType:i.dataType}],dispatchGroup:{x:Math.ceil(he.size(c)/64)},programUniforms:k}),getShaderSource:Z=>Ml(Z,h,i.dims.length,c.length,l,b,$,0,I,O,M,D)}},lf=t=>{let i=t.count_include_pad!==0,n=Nl(t);if(n.ceilMode!==0)throw new Error("using ceil() in shape computation is not yet supported for AveragePool");let u={countIncludePad:i,...n,cacheKey:""};return{...u,cacheKey:of(u)}},df=(t,i)=>{go(t.inputs),t.compute(Pl("AveragePool",t.inputs[0],!1,i))},Ul={autoPad:"",ceilMode:0,countIncludePad:!1,kernelShape:[],strides:[],pads:[],storageOrder:0,dilations:[]},pf=t=>{let i=t.format;return{format:i,...Ul,cacheKey:i}},cf=(t,i)=>{go(t.inputs),t.compute(Pl("GlobalAveragePool",t.inputs[0],!0,i))},Ll=(t,i,n,u)=>{let[l,c]=Rl(i,u,n),h=`
      value = max(x_val, value);
    `,y="",b=oe("x",i.dataType,i.dims.length),$=["rank"],[k,I,O,M,D]=Bl(c,l);return k.push(...ie(i.dims,c)),{name:t,shaderCache:{hint:`${u.cacheKey};${O};${M};${D}`,inputDependencies:$},getRunData:()=>({outputs:[{dims:c,dataType:i.dataType}],dispatchGroup:{x:Math.ceil(he.size(c)/64)},programUniforms:k}),getShaderSource:L=>Ml(L,b,i.dims.length,c.length,l,h,y,i.dataType===10?-65504:-1e5,I,O,M,D)}},hf=(t,i)=>{go(t.inputs),t.compute(Ll("MaxPool",t.inputs[0],!1,i))},ff=t=>{let i=t.storage_order,n=t.dilations,u=Nl(t);if(i!==0)throw new Error("column major storage order is not yet supported for MaxPool");if(u.ceilMode!==0)throw new Error("using ceil() in shape computation is not yet supported for MaxPool");let l={storageOrder:i,dilations:n,...u,cacheKey:""};return{...l,cacheKey:uf(l)}},mf=t=>{let i=t.format;return{format:i,...Ul,cacheKey:i}},gf=(t,i)=>{go(t.inputs),t.compute(Ll("GlobalMaxPool",t.inputs[0],!0,i))}}),yf,_f,wf,bf,Xv=m(()=>{it(),Xe(),j(),Ke(),yf=(t,i)=>{if(t.length<2||t.length>3)throw new Error("DequantizeLinear requires 2 or 3 inputs.");if(t.length===3&&t[1].dims===t[2].dims)throw new Error("x-scale and x-zero-point must have the same shape.");if(t.length===3&&t[0].dataType!==t[2].dataType)throw new Error("x and x-zero-point must have the same data type.");if(t[1].dims.length!==0&&t[1].dims.length!==1&&t[1].dims.length!==t[0].dims.length)throw new Error("scale input must be a scalar, a 1D tensor, or have the same rank as the input tensor.");if(t.length>2){if(t[0].dataType!==t[2].dataType)throw new Error("x and x-zero-point must have the same data type.");if(t[1].dims.length!==t[2].dims.length)throw new Error("scale and zero-point inputs must have the same rank.");if(!t[1].dims.map((n,u)=>n===t[2].dims[u]).reduce((n,u)=>n&&u,!0))throw new Error("scale and zero-point inputs must have the same shape.")}if(i.blockSize>0){if(t[1].dims.length===0||t[1].dims.length===1&&t[1].dims[0]===1)throw new Error("blockSize must be set only for block quantization.");if(!t[1].dims.map((l,c)=>c===i.axis||l===t[0].dims[c]).reduce((l,c)=>l&&c,!0))throw new Error("For block qunatization, scale input shape to match the input shape except for the axis");if(t[1].dims.length!==t[0].dims.length)throw new Error("For block qunatization the scale input rank must be the same as the x rank.");let n=t[0].dims[i.axis],u=t[1].dims[i.axis];if(i.blockSize<Math.ceil(n/u)||i.blockSize>Math.ceil(n/(u-1)-1))throw new Error("blockSize must be with in the range [ceil(dI / Si), ceil(dI / (Si - 1) - 1)].")}},_f=(t,i)=>{let n=he.normalizeAxis(i.axis,t[0].dims.length),u=t[0].dataType,l=u===3,c=t[0].dims,h=t[1].dataType,y=he.size(c),b=u===3||u===2,$=b?[Math.ceil(he.size(t[0].dims)/4)]:t[0].dims,k=t[1].dims,I=t.length>2?t[2]:void 0,O=I?b?[Math.ceil(he.size(I.dims)/4)]:I.dims:void 0,M=k.length===0||k.length===1&&k[0]===1,D=M===!1&&k.length===1,L=le(y),Z=M&&(!b||L===4),W=Z?L:1,V=Z&&!b?L:1,J=oe("input",b?12:u,$.length,V),Y=oe("scale",h,k.length),se=I?oe("zero_point",b?12:u,O.length):void 0,de=ke("output",h,c.length,W),fe=[J,Y];se&&fe.push(se);let we=[$,k];I&&we.push(O);let xe=[{type:12,data:y/W},{type:12,data:n},{type:12,data:i.blockSize},...ie(...we,c)],De=at=>{let et=[{name:"output_size",type:"u32"},{name:"axis",type:"u32"},{name:"block_size",type:"u32"}];return`
      ${at.registerUniforms(et).declareVariables(...fe,de)}
      ${at.mainStart()}
          ${at.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
          let output_indices = ${de.offsetToIndices("global_idx")};

          // Set input x
          ${b?`
            let input = ${J.getByOffset("global_idx / 4")};
            let x_vec = ${l?"unpack4xI8(input)":"unpack4xU8(input)"};
            let x_value = ${W===1?"x_vec[global_idx % 4]":"x_vec"};`:`let x_value = ${J.getByOffset("global_idx")};`};

          // Set scale input
          ${M?`let scale_value= ${Y.getByOffset("0")}`:D?`
            let scale_index = ${de.indicesGet("output_indices","uniforms.axis")};
            let scale_value= ${Y.getByOffset("scale_index")};`:`
            var scale_indices: ${Y.type.indices} = output_indices;
            let index = ${Y.indicesGet("scale_indices","uniforms.axis")} / uniforms.block_size;
            ${Y.indicesSet("scale_indices","uniforms.axis","index")};
            let scale_value= ${Y.getByIndices("scale_indices")};`};

          // Set zero-point input
          ${se?M?b?`
                let zero_point_input = ${se.getByOffset("0")};
                let zero_point_vec =  ${l?"unpack4xI8(zero_point_input)":"unpack4xU8(zero_point_input)"};
                let zero_point_value= zero_point_vec[0]`:`let zero_point_value = ${se.getByOffset("0")}`:D?b?`
                let zero_point_index = ${de.indicesGet("output_indices","uniforms.axis")};
                let zero_point_input = ${se.getByOffset("zero_point_index / 4")};
                let zero_point_vec =  ${l?"unpack4xI8(zero_point_input)":"unpack4xU8(zero_point_input)"};
                let zero_point_value = zero_point_vec[zero_point_index % 4]`:`
                let zero_point_index = ${de.indicesGet("output_indices","uniforms.axis")};
                let zero_point_value = ${se.getByOffset("zero_point_index")};`:b?`
                let zero_point_offset = ${Y.indicesToOffset("scale_indices")};
                let zero_point_input = ${se.getByOffset("zero_point_offset / 4")};
                let zero_point_vec = ${l?"unpack4xI8(zero_point_input)":"unpack4xU8(zero_point_input)"};
                let zero_point_value = zero_point_vec[zero_point_offset % 4];`:`let zero_point_value = ${se.getByIndices("scale_indices")};`:`let zero_point_value = ${b?l?"i32":"u32":J.type.value}(0);`};
      // Compute and write output
      ${de.setByOffset("global_idx",`${de.type.value}(x_value - zero_point_value) * scale_value`)};
      }`};return{name:"DequantizeLinear",shaderCache:{hint:i.cacheKey,inputDependencies:se?["rank","rank","rank"]:["rank","rank"]},getShaderSource:De,getRunData:()=>({outputs:[{dims:c,dataType:h}],dispatchGroup:{x:Math.ceil(y/W/64),y:1,z:1},programUniforms:xe})}},wf=(t,i)=>{yf(t.inputs,i),t.compute(_f(t.inputs,i))},bf=t=>N({axis:t.axis,blockSize:t.blockSize})}),$f,vf,xf,Yv=m(()=>{Kt(),it(),Ke(),$f=(t,i,n)=>{let u=t===i,l=t<i&&n<0,c=t>i&&n>0;if(u||l||c)throw new Error("Range these inputs' contents are invalid.")},vf=(t,i,n,u)=>{let l=Math.abs(Math.ceil((i-t)/n)),c=[l],h=l,y=[{type:12,data:h},{type:u,data:t},{type:u,data:n},...ie(c)],b=$=>{let k=ke("output",u,c.length),I=k.type.value,O=[{name:"outputSize",type:"u32"},{name:"start",type:I},{name:"delta",type:I}];return`
        ${$.registerUniforms(O).declareVariables(k)}
        ${$.mainStart()}
        ${$.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
        output[global_idx] = uniforms.start + ${I}(global_idx) * uniforms.delta;
      }`};return{name:"Range",shaderCache:{hint:`${u}`},getShaderSource:b,getRunData:()=>({outputs:[{dims:c,dataType:u}],dispatchGroup:{x:Math.ceil(h/64)},programUniforms:y})}},xf=t=>{let i=0,n=0,u=0;t.inputs[0].dataType===6?(i=t.inputs[0].getInt32Array()[0],n=t.inputs[1].getInt32Array()[0],u=t.inputs[2].getInt32Array()[0]):t.inputs[0].dataType===1&&(i=t.inputs[0].getFloat32Array()[0],n=t.inputs[1].getFloat32Array()[0],u=t.inputs[2].getFloat32Array()[0]),B.webgpu.validateInputContent&&$f(i,n,u),t.compute(vf(i,n,u,t.inputs[0].dataType),{inputs:[]})}}),Sf,Tf,kf,Ef,Jv=m(()=>{it(),Xe(),j(),Ke(),Sf=(t,i,n,u)=>{if(t!=="none"&&u!=="i32"&&u!=="u32"&&u!=="f32")throw new Error(`Input ${u} is not supported with reduction ${t}.`);let l=`{
                var oldValue = 0;
                loop {
                  let newValueF32 =`,c=`;
                  let newValue = bitcast<i32>(newValueF32);
                  let res = atomicCompareExchangeWeak(&${i}, oldValue, newValue);
                  if res.exchanged {
                    break;
                  }
                  oldValue = res.old_value;
                }
              }`;switch(t){case"none":return`${i}=${n};`;case"add":return u==="i32"||u==="u32"?`atomicAdd(&${i}, bitcast<${u}>(${n}));`:`
              ${l}bitcast<${u}>(oldValue) + (${n})${c}`;case"max":return u==="i32"||u==="u32"?`atomicMax(&${i}, bitcast<${u}>(${n}));`:`
                ${l}max(bitcast<f32>(oldValue), (${n}))${c}`;case"min":return u==="i32"||u==="u32"?`atomicMin(&${i}, bitcast<${u}>(${n}));`:`${l}min(bitcast<${u}>(oldValue), (${n}))${c}`;case"mul":return`${l}(bitcast<${u}>(oldValue) * (${n}))${c}`;default:throw new Error(`Reduction ${t} is not supported.`)}},Tf=(t,i)=>{let n=t[0].dims,u=t[1].dims,l=n,c=1,h=Math.ceil(he.sizeToDimension(u,u.length-1)/c),y=u[u.length-1],b=he.sizeFromDimension(n,y),$=[{type:12,data:h},{type:12,data:y},{type:12,data:b},...ie(t[1].dims,t[2].dims,l)],k=I=>{let O=oe("indices",t[1].dataType,t[1].dims.length),M=oe("updates",t[2].dataType,t[2].dims.length,c),D=i.reduction!=="none"&&i.reduction!==""?qt("output",t[0].dataType,l.length):ke("output",t[0].dataType,l.length,c);return`
      ${I.registerUniform("output_size","u32").registerUniform("last_index_dimension","u32").registerUniform("num_updates_elements","u32").declareVariables(O,M,D)}
      ${I.mainStart()}
        ${I.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
  var data_offset = 0u;
  let indices_start = uniforms.last_index_dimension * global_idx;
  let indices_end = indices_start + uniforms.last_index_dimension;
  for (var i = indices_start; i < indices_end; i++) {
    var index = i32(indices[i].x);
    ${t[0].dims.length===1?`
    let element_count_dim = uniforms.output_strides;
    let dim_value = uniforms.output_shape;`:`
    let element_count_dim = uniforms.output_strides[i - indices_start];
    let dim_value = uniforms.output_shape[i - indices_start];`}
    if (index >= 0) {
      if (index >= i32(dim_value)) {
        index = i32(dim_value - 1);
      }
    } else {
      if (index < -i32(dim_value)) {
        index = 0;
      } else {
        index += i32(dim_value);
      }
    }
    data_offset += u32((u32(index) * element_count_dim));
  }

  for (var i = 0u; i < uniforms.num_updates_elements; i++) {
    let value = updates[uniforms.num_updates_elements * global_idx + i];
    ${Sf(i.reduction,"output[data_offset + i]","value",D.type.value)}
  }

      }`};return{name:"ScatterND",shaderCache:{hint:`${i.cacheKey}_${i.reduction}`,inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:l,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(h/64)},programUniforms:$}),getShaderSource:k}},kf=t=>N({reduction:t.reduction}),Ef=(t,i)=>{t.compute(Tf(t.inputs,i),{inputs:[t.inputs[1],t.inputs[2]],outputs:[]})}}),If,zf,Cf,ql,Af,Of,Rf,Bf,Mf,Df,Nf,Pf,Vl,Uf,Lf,qf,Vf,Wf,Gf,Ff,e2=m(()=>{it(),Xe(),j(),Ke(),If=(t,i)=>{if(t.every(n=>n>0||(()=>{throw new Error("Resize requires scales input values to be positive")})),t.length>0){if(i.mode==="linear"){if(!(t.length===2||t.length===3||t.length===4&&t[0]===1&&t[1]===1||t.length===4&&t[0]===1&&t[3]===1||t.length===5&&t[0]===1&&t[1]===1))throw new Error(`For linear mode, Resize requires scales to be 2D, 3D, 4D with either two outermost or one innermost and
            one outermost scale values equal to 1, or 5D with two outermost scale values equal to 1`)}else if(i.mode==="cubic"&&!(t.length===2||t.length===4&&t[0]===1&&t[1]===1||t.length===4&&t[0]===1&&t[3]===1))throw new Error("Resize requires scales input size to be 2 or 4 for cubic mode")}},zf=(t,i,n)=>{i.every(l=>l>=0&&l<n||(()=>{throw new Error("Resize requires axes input values to be positive and less than rank")}));let u=new Array(n).fill(1);return i.forEach((l,c)=>u[l]=t[c]),u},Cf=(t,i,n,u,l,c)=>{let[h,y,b]=n>10?[1,2,3]:[-1,t.length>1?1:-1,-1],$=t[0].dims.length;if(h>0&&t.length>h&&t[h].dims.length>0)t[h].getFloat32Array().forEach(k=>c.push(k));else if(i.coordinateTransformMode==="tf_crop_and_resize")throw new Error("Resize requires RoI input to be specified when coordinateTransformMode is tfCropAndResize");if(y>0&&t.length>y&&t[y].dims.length===1&&t[y].dims[0]>0){if(t[y].getFloat32Array().forEach(k=>u.push(k)),u.length!==0&&u.length!==$&&n>=18&&u.length!==i.axes.length)throw new Error("Resize requires scales input size to be same as input rank or axes size for opset 18 and up");If(u,i),i.axes.length>0&&zf(u,i.axes,$).forEach((k,I)=>u[I]=k)}if(b>0&&t.length>b&&t[b].dims.length===1&&t[b].dims[0]>0&&(t[b].getBigInt64Array().forEach(k=>l.push(Number(k))),l.length!==0&&l.length!==$&&n>=18&&l.length!==i.axes.length))throw new Error("Resize requires sizes input size to be same as input rank or axes size for opset 18 and up");if(i.axes.length>0){if(u.length!==0&&u.length!==i.axes.length)throw new Error('Resize requires "scales" input size to be of axes rank when axes attributes is specified');if(l.length!==0&&l.length!==i.axes.length)throw new Error('Resize requires "sizes" input size to be of rank axes rank when axes attributes is specified')}if(typeof u<"u"&&typeof l<"u"&&u.length>0&&l.length>$)throw new Error("Resize requires only of scales or sizes to be specified")},ql=(t,i,n,u)=>`
  // The whole part and the fractional part are calculated separately due to inaccuracy of floating
  // point division. As an example, f32(21) / f32(7) may evaluate to 2.99... instead of 3, causing an
  // offset-by-one error later in floor().
  let big = (${t}) * (${i});
  let whole = ${u}(big / (${n}));
  let fract = ${u}(big % (${n})) / ${u}(${n});
  return whole + fract;
`,Af=(t,i)=>`fn getOriginalCoordinateFromResizedCoordinate(xResized: u32, xScale: f32, lengthResized: u32,
     lengthOriginal: u32, roiStart: f32, roiEnd: f32) -> ${i} { `+(()=>{switch(t){case"asymmetric":return`
          if (xScale < 1.0 || floor(xScale) != xScale) {
            return ${i}(xResized) / ${i}(xScale);
          } else {
            ${ql("xResized","lengthOriginal","lengthResized",i)}
          }
        `;case"pytorch_half_pixel":return`if (lengthResized > 1) {
                    return (${i}(xResized) + 0.5) / ${i}(xScale) - 0.5;
                  } else {
                    return 0.0;
                  }`;case"tf_half_pixel_for_nn":return`return (${i}(xResized) + 0.5) / ${i}(xScale);`;case"align_corners":return`if (lengthResized == 1) {
                    return 0.0;
                  } else {
                    ${ql("xResized","lengthOriginal - 1","lengthResized - 1",i)}
                  }`;case"tf_crop_and_resize":return`if (lengthResized > 1) {
                    return ${i}(roiStart) * ${i}(lengthOriginal - 1) +
                        (${i}(xResized) * ${i}(roiEnd - roiStart) * ${i}(lengthOriginal - 1)) /
                        ${i}(lengthResized - 1);
                  } else {
                    return 0.5 * ${i}(roiStart + roiEnd) * ${i}(lengthOriginal - 1);
                  }`;case"half_pixel_symmetric":return`const outputWidth = ${i}xScale * ${i}(lengthResized);
                  const adjustment = ${i}(lengthResized) / outputWidth;
                  const center = ${i}(lengthOriginal) / 2;
                  const offset = center * (1 - adjustment);
                  return offset + ((${i}(xResized) + 0.5) / ${i}(xScale)) - 0.5;`;case"half_pixel":return`return ((${i}(xResized) + 0.5) / ${i}(xScale)) - 0.5;`;default:throw new Error(`Coordinate transform mode ${t} is not supported`)}})()+"}",Of=(t,i,n)=>`fn getNearestPixelFromOriginal(xOriginal: ${n}, isDownSample: bool) -> ${n} {`+(()=>{switch(t){case"round_prefer_ceil":return"if (fract(xOriginal) == 0.5) {             return ceil(xOriginal);           } else {             return round(xOriginal);           }";case"floor":return"return floor(xOriginal);";case"ceil":return"return ceil(xOriginal);";case"round_prefer_floor":return"if (fract(xOriginal) == 0.5) {                     return floor(xOriginal);                   } else {                     return round(xOriginal);                   }";case"simple":default:if(i<11)return"if (isDownSample)                     {                       return ceil(xOriginal);                     } else {                       return xOriginal;                     }";throw new Error(`Nearest mode ${t} is not supported`)}})()+"}",Rf=(t,i,n)=>{let u=new Array(n).fill(0).concat(new Array(n).fill(1)),l=t.length===0?u:t.slice();return i.length>0?(i.forEach((c,h)=>{u[c]=l[h],u[h+n]=l[i.length+h]}),u):l},Bf=(t,i,n,u)=>{let l=[];if(n.length>0)if(u.length>0){if(t.forEach(c=>l.push(c)),Math.max(...u)>t.length)throw new Error("axes is out of bound");u.forEach((c,h)=>l[c]=n[h])}else n.forEach(c=>l.push(c));else{if(i.length===0)throw new Error("Resize requires either scales or sizes.");l=t.map((c,h)=>Math.round(c*i[h]))}return l},Mf=(t,i,n)=>{let u=(()=>{switch(n.keepAspectRatioPolicy){case"not_larger":return n.axes.length>0?Math.min(...n.axes.map(c=>i[c]),Number.MAX_VALUE):Math.min(...i,Number.MAX_VALUE);case"not_smaller":return n.axes.length>0?Math.max(...n.axes.map(c=>i[c]),Number.MIN_VALUE):Math.max(...i,Number.MIN_VALUE);default:throw new Error(`Keep aspect ratio policy ${n.keepAspectRatioPolicy} is not supported`)}})();i.fill(1,0,i.length);let l=t.slice();return n.axes.length>0?(n.axes.forEach(c=>i[c]=u),n.axes.forEach(c=>l[c]=Math.round(t[c]*i[c]))):(i.fill(u,0,i.length),l.forEach((c,h)=>l[h]=Math.round(c*i[h]))),l},Df=(t,i,n,u,l)=>`
    fn calculateOriginalIndicesFromOutputIndices(output_indices: ${t.type.indices}) -> array<${t.type.value}, ${n.length}> {
      var original_indices: array<${t.type.value}, ${n.length}>;
      for (var i:u32 = 0; i < ${n.length}; i++) {
        var output_index = ${t.indicesGet("output_indices","i")};
        var scale = ${ce("uniforms.scales","i",u)};
        var roi_low = ${ce("uniforms.roi","i",l)};
        var roi_hi = ${ce("uniforms.roi",`i + ${i.length}`,l)};
        if (scale == 1.0) {
          original_indices[i] = ${t.type.value}(output_index);
        } else {
          var input_shape_i = ${ce("uniforms.input_shape","i",i.length)};
          var output_shape_i = ${ce("uniforms.output_shape","i",n.length)};
          original_indices[i] = getOriginalCoordinateFromResizedCoordinate(output_index, scale, output_shape_i,
                                                                           input_shape_i, roi_low, roi_hi);
        }
      }
      return original_indices;
    }`,Nf=(t,i,n,u,l,c,h)=>`
    fn calculateInputIndicesFromOutputIndices(output_indices: ${i.type.indices}) -> ${t.type.indices} {
      var input_indices: ${t.type.indices};
      for (var i:u32 = 0; i < ${u.length}; i++) {
        var output_index = ${i.indicesGet("output_indices","i")};
        var input_index: u32;
        var scale = ${ce("uniforms.scales","i",l)};
        if (scale == 1.0) {
          input_index = output_index;
        } else {
          var roi_low = ${ce("uniforms.roi","i",c)};
          var roi_hi = ${ce("uniforms.roi",`i + ${n.length}`,c)};
          var input_shape_i = ${ce("uniforms.input_shape","i",n.length)};
          var output_shape_i = ${ce("uniforms.output_shape","i",u.length)};
          var original_idx = getOriginalCoordinateFromResizedCoordinate(output_index, scale, output_shape_i,
                                                                        input_shape_i, roi_low, roi_hi);
          if (!${h} || (original_idx >= 0 && original_idx < ${i.type.value}(input_shape_i))) {
            if (original_idx < 0) {
              input_index = 0;
            } else if (original_idx > ${i.type.value}(input_shape_i - 1)) {
              input_index = input_shape_i - 1;
            } else {
              input_index = u32(getNearestPixelFromOriginal(original_idx, scale < 1));
            }
          } else {
            input_index = u32(original_idx);
          }
        }
        ${t.indicesSet("input_indices","i","input_index")}
      }
      return input_indices;
    }`,Pf=(t,i)=>`
    fn checkInputIndices(input_indices: ${t.type.indices}) -> bool {
      for (var i:u32 = 0; i < ${i.length}; i++) {
        var input_index = ${t.indicesGet("input_indices","i")};
        if (input_index < 0 || input_index >= ${ce("uniforms.input_shape","i",i.length)}) {
          return false;
        }
      }
      return true;
    }`,Vl=(t,i,n,u)=>t.rank>u?`
    ${t.indicesSet("input_indices",i,"channel")};
    ${t.indicesSet("input_indices",n,"batch")};
`:"",Uf=(t,i,n,u,l)=>{let[c,h,y,b]=n.length===2?[-1,0,1,-1]:[0,2,3,1],$=t.type.value;return`
    fn getInputValue(batch: u32, channel: u32, row: u32, col: u32) -> ${$} {
      var input_indices: ${t.type.indices};
      ${t.indicesSet("input_indices",h,`max(0, min(row, ${n[h]} - 1))`)};
      ${t.indicesSet("input_indices",y,`max(0, min(col, ${n[y]} - 1))`)};
      ${Vl(t,b,c,2)}
      return ${t.getByIndices("input_indices")};
    }

    fn bilinearInterpolation(output_indices: ${i.type.indices}) -> ${$} {
      var originalIndices = calculateOriginalIndicesFromOutputIndices(output_indices);
      var row:${$} = originalIndices[${h}];
      var col:${$} = originalIndices[${y}];
      ${u?`if (row < 0 || row > (${n[h]} - 1) || col < 0 || col > (${n[y]} - 1)) {
        return ${l};
      }`:""};
      row = max(0, min(row, ${n[h]} - 1));
      col = max(0, min(col, ${n[y]} - 1));
      var row1: u32 = u32(row);
      var col1: u32 = u32(col);
      var row2: u32 = u32(row + 1);
      var col2: u32 = u32(col + 1);
      var channel: u32 = ${n.length>2?`u32(originalIndices[${b}])`:"0"};
      var batch: u32 =  ${n.length>2?`u32(originalIndices[${c}])`:"0"};
      var x11: ${$} = getInputValue(batch, channel, row1, col1);
      var x12: ${$} = getInputValue(batch, channel, row1, col2);
      var x21: ${$} = getInputValue(batch, channel, row2, col1);
      var x22: ${$} = getInputValue(batch, channel, row2, col2);
      var dx1: ${$} = abs(row - ${$}(row1));
      var dx2: ${$} = abs(${$}(row2) - row);
      var dy1: ${$} = abs(col - ${$}(col1));
      var dy2: ${$} = abs(${$}(col2) - col);
      if (row1 == row2) {
        dx1 = 0.5;
        dx2 = 0.5;
      }
      if (col1 == col2) {
        dy1 = 0.5;
        dy2 = 0.5;
      }
      return (x11 * dx2 * dy2 + x12 * dx2 * dy1 + x21 * dx1 * dy2 + x22 * dx1 * dy1);
    }`},Lf=(t,i,n,u,l,c,h,y,b,$)=>{let k=n.length===2,[I,O]=k?[0,1]:[2,3],M=t.type.value,D=L=>{let Z=L===I?"row":"col";return`
      fn ${Z}CubicInterpolation(input_indices: ${t.type.indices}, output_indices: ${i.type.indices}) -> ${M} {
        var output_index = ${i.indicesGet("output_indices",L)};
        var originalIdx: ${M} = getOriginalCoordinateFromResizedCoordinate(output_index, ${l[L]},
        ${u[L]}, ${n[L]}, ${c[L]}, ${c[L]} + ${n.length});
        var fractOriginalIdx: ${M} = originalIdx - floor(originalIdx);
        var coefs = getCubicInterpolationCoefs(fractOriginalIdx);

        if (${y} && (originalIdx < 0 || originalIdx > (${n[L]} - 1))) {
          return ${b};
        }
        var data: array<${M}, 4> = array<${M}, 4>(0.0, 0.0, 0.0, 0.0);
        for (var i: i32 = -1; i < 3; i++) {
          var ${Z}: ${M} = originalIdx + ${M}(i);
          if (${Z} < 0 || ${Z} >= ${n[L]}) {
            ${$?`coefs[i + 1] = 0.0;
                        continue;`:y?`return ${b};`:`${Z} = max(0, min(${Z}, ${n[L]} - 1));`};
          }
        var input_indices_copy: ${t.type.indices} = input_indices;
          ${t.indicesSet("input_indices_copy",L,`u32(${Z})`)};
          data[i + 1] = ${L===I?t.getByIndices("input_indices_copy"):"rowCubicInterpolation(input_indices_copy, output_indices)"};
        }
        return cubicInterpolation1D(data, coefs);
      }`};return`
    ${D(I)};
    ${D(O)};
  fn getCubicInterpolationCoefs(s: ${M}) -> array<${M}, 4> {
    var absS = abs(s);
    var coeffs: array<${M}, 4> = array<${M}, 4>(0.0, 0.0, 0.0, 0.0);
    var oneMinusAbsS: ${M} = 1.0 - absS;
    var twoMinusAbsS: ${M} = 2.0 - absS;
    var onePlusAbsS: ${M} = 1.0 + absS;
    coeffs[0] = ((${h} * onePlusAbsS - 5 * ${h}) * onePlusAbsS + 8 * ${h}) * onePlusAbsS - 4 * ${h};
    coeffs[1] = ((${h} + 2) * absS - (${h} + 3)) * absS * absS + 1;
    coeffs[2] = ((${h} + 2) * oneMinusAbsS - (${h} + 3)) * oneMinusAbsS * oneMinusAbsS + 1;
    coeffs[3] = ((${h} * twoMinusAbsS - 5 * ${h}) * twoMinusAbsS + 8 * ${h}) * twoMinusAbsS - 4 * ${h};
    return coeffs;
  }

  fn cubicInterpolation1D(x: array<${M}, 4>, coefs: array<${M}, 4>) -> ${M} {
    var coefsSum: ${M} = coefs[0] + coefs[1] + coefs[2] + coefs[3];
    return (x[0] * coefs[0] + x[1] * coefs[1]+ x[2] * coefs[2]+ x[3] * coefs[3]) / coefsSum;
  }

  fn bicubicInterpolation(output_indices: ${i.type.indices}) -> ${M} {
    var input_indices: ${t.type.indices} = output_indices;
    return colCubicInterpolation(input_indices, output_indices);
  }
    `},qf=(t,i,n,u,l)=>{let[c,h,y,b,$]=n.length===3?[-1,0,1,2,-1]:[0,2,3,4,1],k=t.type.value;return`
    fn getInputValue(batch: u32, channel: u32, depth:u32, height: u32, width: u32) -> ${k} {
      var input_indices: ${t.type.indices};
      ${t.indicesSet("input_indices",h,`max(0, min(depth, ${n[h]} - 1))`)};
      ${t.indicesSet("input_indices",y,`max(0, min(height, ${n[y]} - 1))`)};
      ${t.indicesSet("input_indices",b,`max(0, min(width, ${n[b]} - 1))`)};
      ${Vl(t,$,c,3)}
      return ${t.getByIndices("input_indices")};
    }

    fn trilinearInterpolation(output_indices: ${i.type.indices}) -> ${k} {
      var originalIndices = calculateOriginalIndicesFromOutputIndices(output_indices);
      var depth:${k} = originalIndices[${h}];
      var height:${k} = originalIndices[${y}];
      var width:${k} = originalIndices[${b}];
      ${u?`if (depth < 0 || depth > (${n[h]} - 1) || height < 0 || height > (${n[y]} - 1) || width < 0 || (width > ${n[b]} - 1)) {
      return ${l};
        }`:""};

    depth = max(0, min(depth, ${n[h]} - 1));
      height = max(0, min(height, ${n[y]} - 1));
      width = max(0, min(width, ${n[b]} - 1));
      var depth1: u32 = u32(depth);
      var height1: u32 = u32(height);
      var width1: u32 = u32(width);
      var depth2: u32 = u32(depth + 1);
      var height2: u32 = u32(height + 1);
      var width2: u32 = u32(width + 1);
      var channel: u32 = ${n.length>3?`u32(originalIndices[${$}])`:"0"};
      var batch: u32 =  ${n.length>3?`u32(originalIndices[${c}])`:"0"};

      var x111: ${k} = getInputValue(batch, channel, depth1, height1, width1);
      var x112: ${k} = getInputValue(batch, channel, depth1, height1, width2);
      var x121: ${k} = getInputValue(batch, channel, depth1, height2, width1);
      var x122: ${k} = getInputValue(batch, channel, depth1, height2, width2);
      var x211: ${k} = getInputValue(batch, channel, depth2, height1, width1);
      var x212: ${k} = getInputValue(batch, channel, depth2, height1, width2);
      var x221: ${k} = getInputValue(batch, channel, depth2, height2, width1);
      var x222: ${k} = getInputValue(batch, channel, depth2, height2, width2);
      var dx1: ${k} = abs(depth - ${k}(depth1));
      var dx2: ${k} = abs(${k}(depth2) - depth);
      var dy1: ${k} = abs(height - ${k}(height1));
      var dy2: ${k} = abs(${k}(height2) - height);
      var dz1: ${k} = abs(width - ${k}(width1));
      var dz2: ${k} = abs(${k}(width2) - width);
      if (depth1 == depth2) {
        dx1 = 0.5;
        dx2 = 0.5;
      }
      if (height1 == height2) {
        dy1 = 0.5;
        dy2 = 0.5;
      }
      if (width1 == width2) {
        dz1 = 0.5;
        dz2 = 0.5;
      }
      return (x111 * dx2 * dy2 * dz2 + x112 * dx2 * dy2 * dz1 + x121 * dx2 * dy1 *dz2 + x122 * dx2 * dy1 * dz1 +
              x211 * dx1 * dy2 * dz2 + x212 * dx1 * dy2 * dz1 + x221 * dx1 * dy1 *dz2 + x222 * dx1 * dy1 * dz1);
    }`},Vf=(t,i,n,u,l,c)=>{let h=t.dims,y=Rf(c,i.axes,h.length),b=Bf(h,u,l,i.axes),$=u.slice();u.length===0&&($=h.map((V,J)=>V===0?1:b[J]/V),i.keepAspectRatioPolicy!=="stretch"&&(b=Mf(h,$,i)));let k=ke("output",t.dataType,b.length),I=oe("input",t.dataType,h.length),O=he.size(b),M=h.length===b.length&&h.every((V,J)=>V===b[J]),D=i.coordinateTransformMode==="tf_crop_and_resize",L=i.extrapolationValue,Z=I.type.value,W=V=>`
      ${M?"":`
      ${Af(i.coordinateTransformMode,Z)};
      ${(()=>{switch(i.mode){case"nearest":return`
              ${Pf(I,h)};
              ${Of(i.nearestMode,n,Z)};
              ${Nf(I,k,h,b,$.length,y.length,D)};
              `;case"linear":return`
              ${Df(k,h,b,$.length,y.length)};
              ${(()=>{if(h.length===2||h.length===4)return`${Uf(I,k,h,D,L)}`;if(h.length===3||h.length===5)return`${qf(I,k,h,D,L)}`;throw Error("Linear mode only supports input dims 2, 3, 4 and 5 are supported in linear mode.")})()};
            `;case"cubic":return`
            ${(()=>{if(h.length===2||h.length===4)return`${Lf(I,k,h,b,$,y,i.cubicCoeffA,D,i.extrapolationValue,i.excludeOutside)}`;throw Error("Cubic mode only supports input dims 2 and 4 are supported in linear mode.")})()};
            `;default:throw Error("Invalid resize mode")}})()};
      `}
      ${V.registerUniform("output_size","u32").registerUniform("scales","f32",$.length).registerUniform("roi","f32",y.length).declareVariables(I,k)}
      ${V.mainStart()}
        ${V.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
        ${M?"output[global_idx] = input[global_idx];":`
        let output_indices = ${k.offsetToIndices("global_idx")};
        var input_indices: ${I.type.indices};
        ${(()=>{switch(i.mode){case"nearest":return`input_indices = calculateInputIndicesFromOutputIndices(output_indices);
                if (checkInputIndices(input_indices)) {
                  output[global_idx] = ${I.getByIndices("input_indices")};
                } else {
                  output[global_idx] = ${i.extrapolationValue};
                }`;case"linear":return`output[global_idx] = ${h.length===2||h.length===4?"bilinearInterpolation":"trilinearInterpolation"}(output_indices);`;case"cubic":return"output[global_idx] = bicubicInterpolation(output_indices);";default:throw Error(`Unsupported resize mode: ${i.mode}`)}})()};
`}
      }`;return{name:"Resize",shaderCache:{hint:`${i.cacheKey}|${n}|${$.length>0?i.mode==="cubic"?$:$.length:""}|${l.length>0?l:""}|${y.length>0?y:""}|${M}|${i.mode==="nearest"?h.length:h}`,inputDependencies:["rank"]},getShaderSource:W,getRunData:()=>({outputs:[{dims:b,dataType:t.dataType}],dispatchGroup:{x:Math.ceil(O/64)},programUniforms:[{type:12,data:O},{type:1,data:$},{type:1,data:y},...ie(h,b)]})}},Wf=t=>{let i=t.customDataBuffer;return new Uint32Array(i,i.byteOffset,1)[0]},Gf=(t,i)=>{let n=[],u=[],l=[],c=Wf(t);if(i.antialias!==0)throw Error("Only default value (0) for Antialias attribute is supported");Cf(t.inputs,i,c,n,u,l),t.compute(Vf(t.inputs[0],i,c,n,u,l),{inputs:[0]})},Ff=t=>{let i=t.antialias,n=t.axes,u=t.coordinateTransformMode,l=t.cubicCoeffA,c=t.excludeOutside!==0,h=t.extrapolationValue,y=t.keepAspectRatioPolicy,b=t.mode,$=t.nearestMode===""?"simple":t.nearestMode;return N({antialias:i,axes:n,coordinateTransformMode:u,cubicCoeffA:l,excludeOutside:c,extrapolationValue:h,keepAspectRatioPolicy:y,mode:b,nearestMode:$})}}),Hf,jf,Kf,t2=m(()=>{it(),Xe(),Ke(),Hf=t=>{if(!t||t.length<3)throw new Error("layerNorm requires at least 3 inputs.");let i=t[0],n=t[1],u=t[2];if(i.dataType!==n.dataType||i.dataType!==u.dataType)throw new Error("All inputs must have the same data type");if(i.dims.length!==3&&i.dims.length!==2)throw new Error("Input must be 2D or 3D");if(n.dims.length!==3&&n.dims.length!==2)throw new Error("Skip must be 2D or 3D");let l=i.dims[i.dims.length-1],c=i.dims[i.dims.length-2];if(n.dims[n.dims.length-1]!==l)throw new Error("Skip must have the same hidden size as input");if(n.dims[n.dims.length-2]!==c)throw new Error("Skip must have the same sequence length as input");if(u.dims.length!==1)throw new Error("Gamma must be 1D");if(u.dims[u.dims.length-1]!==l)throw new Error("Gamma must have the same hidden size as input");if(t.length>3){let h=t[3];if(h.dims.length!==1)throw new Error("Beta must be 1D");if(h.dims[h.dims.length-1]!==l)throw new Error("Beta must have the same hidden size as input")}if(t.length>4){let h=t[4];if(h.dims.length!==1)throw new Error("Bias must be 1D");if(h.dims[h.dims.length-1]!==l)throw new Error("Bias must have the same hidden size as input")}},jf=(t,i,n,u)=>{let l=i.simplified,c=t[0].dims,h=he.size(c),y=c,b=h,$=c.slice(-1)[0],k=u?c.slice(0,-1).concat(1):[],I=!l&&t.length>3,O=t.length>4,M=u&&n>1,D=u&&n>2,L=n>3,Z=64,W=le($),V=[{type:12,data:b},{type:12,data:W},{type:12,data:$},{type:1,data:i.epsilon}],J=se=>{let de=[{name:"output_size",type:"u32"},{name:"components",type:"u32"},{name:"hidden_size",type:"u32"},{name:"epsilon",type:"f32"}],fe=[oe("x",t[0].dataType,t[0].dims,W),oe("skip",t[1].dataType,t[1].dims,W),oe("gamma",t[2].dataType,t[2].dims,W)];I&&fe.push(oe("beta",t[3].dataType,t[3].dims,W)),O&&fe.push(oe("bias",t[4].dataType,t[4].dims,W)),fe.push(ke("output",t[0].dataType,y,W)),M&&fe.push(ke("mean_output",1,k)),D&&fe.push(ke("inv_std_output",1,k)),L&&fe.push(ke("input_skip_bias_sum",t[0].dataType,y,W));let we=ue(t[0].dataType),xe=ue(1,W);return`

      ${se.registerUniforms(de).declareVariables(...fe)}
      var<workgroup> sum_shared : array<${xe}, ${Z}>;
      var<workgroup> sum_squared_shared : array<${xe}, ${Z}>;

      ${se.mainStart([Z,1,1])}
        let ix = local_id.x;
        let iy = global_id.x / ${Z};

        let hidden_size_vectorized: u32 = uniforms.hidden_size / uniforms.components;
        var stride = hidden_size_vectorized / ${Z};
        let offset = ix * stride + iy * hidden_size_vectorized;
        let offset1d = stride * ix;
        if (ix == ${Z-1}) {
          stride = hidden_size_vectorized - stride * ix;
        }
        for (var i: u32 = 0; i < stride; i++) {
          let skip_value = skip[offset + i];
          let bias_value = ${O?"bias[offset1d + i]":we+"(0.0)"};
          let input_value = x[offset + i];
          let value = input_value + skip_value + bias_value;
          ${L?"input_skip_bias_sum[offset + i] = value;":""}
          output[offset + i] = value;
          let f32_value = ${Se(we,W,"value")};
          sum_shared[ix] += f32_value;
          sum_squared_shared[ix] += f32_value * f32_value;
        }
        workgroupBarrier();

        var reduce_size : u32 = ${Z};
        for (var curr_size = reduce_size >> 1;  curr_size > 0; curr_size = reduce_size >> 1) {
          reduce_size = curr_size + (reduce_size & 1);
          if (ix < curr_size) {
            sum_shared[ix] += sum_shared[ix + reduce_size];
            sum_squared_shared[ix] += sum_squared_shared[ix + reduce_size];
          }
          workgroupBarrier();
        }

        let sum = sum_shared[0];
        let square_sum = sum_squared_shared[0];
        let mean = ${be("sum",W)} / f32(uniforms.hidden_size);
        let inv_std_dev = inverseSqrt(${be("square_sum",W)} / f32(uniforms.hidden_size) ${l?"":"- mean * mean"} + uniforms.epsilon);
        ${M?"mean_output[global_idx] = mean;":""}
        ${D?"inv_std_output[global_idx] = inv_std_dev;":""}

        for (var i: u32 = 0; i < stride; i++) {
          output[offset + i] = (output[offset + i] ${l?"":`- ${we}(mean)`}) *
            ${we}(inv_std_dev) * gamma[offset1d + i]
            ${I?"+ beta[offset1d + i]":""};
        }
      }`},Y=[{dims:y,dataType:t[0].dataType}];return n>1&&Y.push({dims:k,dataType:1}),n>2&&Y.push({dims:k,dataType:1}),n>3&&Y.push({dims:c,dataType:t[0].dataType}),{name:"SkipLayerNormalization",shaderCache:{hint:`${W};${M};${D};${L}`,inputDependencies:t.map((se,de)=>"type")},getShaderSource:J,getRunData:()=>({outputs:Y,dispatchGroup:{x:Math.ceil(b/$)},programUniforms:V})}},Kf=(t,i)=>{Hf(t.inputs);let n=[0];t.outputCount>1&&n.push(-3),t.outputCount>2&&n.push(-3),t.outputCount>3&&n.push(3),t.compute(jf(t.inputs,i,t.outputCount,!1),{outputs:n})}}),Zf,yo,Qf,Wl,Xf,Yf,Jf,em,r2=m(()=>{it(),Xe(),j(),Ke(),Zf=(t,i)=>{if(!t||t.length<1)throw new Error("too few inputs");if(i.axes.length!==0){if(i.axes.length!==i.starts.length||i.axes.length!==i.ends.length)throw new Error("axes, starts and ends must have the same length")}else if(i.starts.length!==i.ends.length)throw new Error("starts and ends must have the same length");t.slice(1).forEach((n,u)=>{if(t[u+1].dataType!==6&&t[u+1].dataType!==7)throw new Error(`Input ${u} must be an array of int32 or int64`)})},yo=(t,i)=>{let n=[];if(t.length>i)if(t[i].dataType===7)t[i].getBigInt64Array().forEach(u=>n.push(Number(u)));else if(t[i].dataType===6)t[i].getInt32Array().forEach(u=>n.push(Number(u)));else throw new Error(`Input ${i} must be an array of int32 or int64`);return n},Qf=(t,i)=>{if(t.length>1){let n=yo(t,1),u=yo(t,2),l=yo(t,3);return l.length===0&&(l=[...Array(t[0].dims.length).keys()]),N({starts:n,ends:u,axes:l})}else return i},Wl=(t,i,n,u,l)=>{let c=t;return t<0&&(c+=n[u[i]]),l[i]<0?Math.max(0,Math.min(c,n[u[i]]-1)):Math.max(0,Math.min(c,n[u[i]]))},Xf=(t,i,n)=>`fn calculateInputIndices(output_indices: ${i.type.indices}) -> ${t.type.indices} {
          var input_indices: ${t.type.indices};
          var carry = 0u;
          for (var i = ${n.length-1}; i >= 0; i--) {
            let input_shape_i = ${ce("uniforms.input_shape","i",n.length)};
            let steps_i = ${ce("uniforms.steps","i",n.length)};
            let signs_i = ${ce("uniforms.signs","i",n.length)};
            let starts_i = ${ce("uniforms.starts","i",n.length)};
            var output_index = ${i.indicesGet("output_indices","i")};
            var input_index = output_index * steps_i + starts_i + carry;
            carry = input_index / input_shape_i;
            input_index = input_index % input_shape_i;
            if (signs_i < 0) {
              input_index = input_shape_i - input_index - 1u + starts_i;
            }
            ${t.indicesSet("input_indices","i","input_index")};
          }
          return input_indices;
      }`,Yf=(t,i)=>{let n=t[0].dims,u=he.size(n),l=i.axes.length>0?he.normalizeAxes(i.axes,n.length):[...Array(n.length).keys()],c=yo(t,4);c.forEach(W=>W!==0||(()=>{throw new Error("step cannot be 0")})),c.length===0&&(c=Array(l.length).fill(1));let h=i.starts.map((W,V)=>Wl(W,V,n,l,c)),y=i.ends.map((W,V)=>Wl(W,V,n,l,c));if(l.length!==h.length||l.length!==y.length)throw new Error("start, ends and axes should have the same number of elements");if(l.length!==n.length)for(let W=0;W<n.length;++W)l.includes(W)||(h.splice(W,0,0),y.splice(W,0,n[W]),c.splice(W,0,1));let b=c.map(W=>Math.sign(W));c.forEach((W,V,J)=>{if(W<0){let Y=(y[V]-h[V])/W,se=h[V],de=se+Y*c[V];h[V]=de,y[V]=se,J[V]=-W}});let $=n.slice(0);l.forEach((W,V)=>{$[W]=Math.ceil((y[W]-h[W])/c[W])});let k={dims:$,dataType:t[0].dataType},I=ke("output",t[0].dataType,$.length),O=oe("input",t[0].dataType,t[0].dims.length),M=he.size($),D=[{name:"outputSize",type:"u32"},{name:"starts",type:"u32",length:h.length},{name:"signs",type:"i32",length:b.length},{name:"steps",type:"u32",length:c.length}],L=[{type:12,data:M},{type:12,data:h},{type:6,data:b},{type:12,data:c},...ie(t[0].dims,$)],Z=W=>`
      ${W.registerUniforms(D).declareVariables(O,I)}
        ${Xf(O,I,n)}
        ${W.mainStart()}
          ${W.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
          let output_indices = ${I.offsetToIndices("global_idx")};
          let input_indices = calculateInputIndices(output_indices);
          ${I.setByOffset("global_idx",O.getByIndices("input_indices"))}
      }`;return{name:"Slice",shaderCache:{hint:`${b.length}_${h.length}_${c.length}`,inputDependencies:["rank"]},getShaderSource:Z,getRunData:()=>({outputs:[k],dispatchGroup:{x:Math.ceil(u/64)},programUniforms:L})}},Jf=(t,i)=>{Zf(t.inputs,i);let n=Qf(t.inputs,i);t.compute(Yf(t.inputs,n),{inputs:[0]})},em=t=>{let i=t.starts,n=t.ends,u=t.axes;return N({starts:i,ends:n,axes:u})}}),tm,rm,im,am,i2=m(()=>{it(),Xe(),j(),or(),Ke(),tm=t=>{if(!t||t.length!==1)throw new Error("Softmax op requires 1 input.")},rm=(t,i)=>{let n=t.inputs[0],u=n.dims,l=he.size(u),c=u.length,h=he.normalizeAxis(i.axis,c),y=h<u.length-1,b,$=[];y?($=Array.from({length:c},(fe,we)=>we),$[h]=c-1,$[c-1]=h,b=t.compute(cr(n,$),{inputs:[n],outputs:[-1]})[0]):b=n;let k=b.dims,I=k[c-1],O=l/I,M=le(I),D=I/M,L=64;O===1&&(L=256);let Z=(fe,we)=>we===4?`max(max(${fe}.x, ${fe}.y), max(${fe}.z, ${fe}.w))`:we===2?`max(${fe}.x, ${fe}.y)`:we===3?`max(max(${fe}.x, ${fe}.y), ${fe}.z)`:fe,W=oe("x",b.dataType,b.dims,M),V=ke("result",b.dataType,b.dims,M),J=W.type.value,Y=ue(b.dataType)==="f32"?`var threadMax = ${J}(-3.4028234663852886e+38f);`:`var threadMax = ${J}(-65504.0h);`,se=fe=>`
      var<workgroup> rowMaxShared : ${J};
      var<workgroup> rowSumShared : ${J};
      var<workgroup> threadShared : array<${J}, ${L}>;

      fn getValue(row: i32, col: i32, row_stride: i32) -> ${J} {
        let index = row * row_stride + col;
        return x[index];
      }

      fn setValue(row: i32, col: i32, row_stride: i32, value: ${J}) {
        let index = row * row_stride + col;
        result[index] = value;
      }
      ${fe.registerUniform("packedCols","i32").declareVariables(W,V)}
      ${fe.mainStart(L)}
        let gindex = i32(global_idx);
        let lindex = i32(local_idx);
        const wg = ${L};
        let row = gindex / wg;
        let cols = uniforms.packedCols;
        let row_stride : i32 = uniforms.packedCols;

        // find the rows max
        ${Y}
        for (var col = lindex; col < cols; col += wg) {
          let value = getValue(row, col, row_stride);
          threadMax = max(threadMax, value);
        }
        if (lindex < cols) {
          threadShared[lindex] = threadMax;
        }
        workgroupBarrier();

        var reduceSize = min(cols, wg);
        for (var currSize = reduceSize >> 1;  currSize > 0; currSize = reduceSize >> 1) {
          reduceSize = currSize + (reduceSize & 1);
          if (lindex < currSize) {
            threadShared[lindex] = max(threadShared[lindex], threadShared[lindex + reduceSize]);
          }
          workgroupBarrier();
        }
        if (lindex == 0) {
          rowMaxShared = ${J}(${Z("threadShared[0]",M)});
        }
        workgroupBarrier();

        // find the rows sum
        var threadSum = ${J}(0.0);
        for (var col = lindex; col < cols; col += wg) {
          let subExp = exp(getValue(row, col, row_stride) - rowMaxShared);
          threadSum += subExp;
        }
        threadShared[lindex] = threadSum;
        workgroupBarrier();

        for (var currSize = wg >> 1;  currSize > 0; currSize = currSize >> 1) {
          if (lindex < currSize) {
            threadShared[lindex] = threadShared[lindex] + threadShared[lindex + currSize];
          }
          workgroupBarrier();
        }
        if (lindex == 0) {
          rowSumShared = ${J}(${be("threadShared[0]",M)});
        }
        workgroupBarrier();

        // calculate final value for each element in the row
        for (var col = lindex; col < cols; col += wg) {
          var value = exp(getValue(row, col, row_stride) - rowMaxShared) / rowSumShared;
          // max operation protects against NaN since all values should be >=0
          value = max(value, ${J}(0.0));
          setValue(row, col, row_stride, value);
        }
      }`,de=t.compute({name:"Softmax",shaderCache:{hint:`${M};${L}`,inputDependencies:["type"]},getRunData:()=>({outputs:[{dims:k,dataType:b.dataType}],dispatchGroup:{x:O},programUniforms:[{type:6,data:D}]}),getShaderSource:se},{inputs:[b],outputs:[y?-1:0]})[0];y&&t.compute(cr(de,$),{inputs:[de]})},im=(t,i)=>{tm(t.inputs),rm(t,i)},am=t=>N({axis:t.axis})}),Gl,nm,sm,om,um,a2=m(()=>{it(),Xe(),Ke(),Gl=t=>Array.from(t.getBigInt64Array(),Number),nm=t=>{if(!t||t.length!==2)throw new Error("Tile requires 2 inputs.");if(t[0].dataType!==1&&t[0].dataType!==10&&t[0].dataType!==6&&t[0].dataType!==12)throw new Error("Tile only support float, float16, int32, and uint32 data types");if(t[1].dataType!==7)throw new Error("Tile `repeats` input should be of int64 data type");if(t[1].dims.length!==1)throw new Error("Tile `repeats` input should be 1-D");if(Gl(t[1]).length!==t[0].dims.length)throw new Error("Tile `repeats` input should have same number of elements as rank of input data tensor")},sm=(t,i)=>{let n=[];for(let u=0;u<t.length;++u)n.push(t[u]*i[u]);return n},om=(t,i)=>{let n=t[0].dims,u=i??Gl(t[1]),l=sm(n,u),c=he.size(l),h=t[0].dataType,y=oe("input",h,n.length),b=ke("output",h,l.length),$=k=>`
      const inputShape = ${y.indices(...n)};
      ${k.registerUniform("output_size","u32").declareVariables(y,b)}
      ${k.mainStart()}
      ${k.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
      let output_indices = ${b.offsetToIndices("global_idx")};
      var input_indices: ${y.type.indices};
      for (var i = 0; i < ${n.length}; i++) {
        let input_dim_i = ${y.indicesGet("uniforms.input_shape","i")};
        let input_dim_value = ${b.indicesGet("output_indices","i")}  % input_dim_i;

        ${y.indicesSet("input_indices","i","input_dim_value")}
      }
      ${b.setByOffset("global_idx",y.getByIndices("input_indices"))}
    }`;return{name:"Tile",shaderCache:{hint:`${u}`,inputDependencies:["rank"]},getRunData:()=>({outputs:[{dims:l,dataType:t[0].dataType}],dispatchGroup:{x:Math.ceil(c/64)},programUniforms:[{type:12,data:c},...ie(t[0].dims,l)]}),getShaderSource:$}},um=t=>{nm(t.inputs),t.compute(om(t.inputs),{inputs:[0]})}}),lm,dm,pm,n2=m(()=>{it(),Xe(),Ke(),lm=(t,i,n,u,l)=>{let c=ke("output_data",l,n.length,4),h=oe("a_data",i[1].dataType,i[1].dims.length,4),y=oe("b_data",i[2].dataType,i[2].dims.length,4),b=oe("c_data",i[0].dataType,i[0].dims.length,4),$,k=(I,O,M)=>`select(${O}, ${I}, ${M})`;if(!u)$=c.setByOffset("global_idx",k(h.getByOffset("global_idx"),y.getByOffset("global_idx"),b.getByOffset("global_idx")));else{let I=(O,M,D="")=>{let L=`a_data[index_a${M}][component_a${M}]`,Z=`b_data[index_b${M}][component_b${M}]`,W=`bool(c_data[index_c${M}] & (0xffu << (component_c${M} * 8)))`;return`
            let output_indices${M} = ${c.offsetToIndices(`global_idx * 4u + ${M}u`)};
            let offset_a${M} = ${h.broadcastedIndicesToOffset(`output_indices${M}`,c)};
            let offset_b${M} = ${y.broadcastedIndicesToOffset(`output_indices${M}`,c)};
            let offset_c${M} = ${b.broadcastedIndicesToOffset(`output_indices${M}`,c)};
            let index_a${M} = offset_a${M} / 4u;
            let index_b${M} = offset_b${M} / 4u;
            let index_c${M} = offset_c${M} / 4u;
            let component_a${M} = offset_a${M} % 4u;
            let component_b${M} = offset_b${M} % 4u;
            let component_c${M} = offset_c${M} % 4u;
            ${O}[${M}] = ${D}(${k(L,Z,W)});
          `};l===9?$=`
            var data = vec4<u32>(0);
            ${I("data",0,"u32")}
            ${I("data",1,"u32")}
            ${I("data",2,"u32")}
            ${I("data",3,"u32")}
            output_data[global_idx] = dot(vec4<u32>(0x1, 0x100, 0x10000, 0x1000000), vec4<u32>(data));`:$=`
            ${I("output_data[global_idx]",0)}
            ${I("output_data[global_idx]",1)}
            ${I("output_data[global_idx]",2)}
            ${I("output_data[global_idx]",3)}
          `}return`
        ${t.registerUniform("vec_size","u32").declareVariables(b,h,y,c)}
        ${t.mainStart()}
        ${t.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}
        ${$}
      }`},dm=t=>{let i=t[1].dims,n=t[2].dims,u=t[0].dims,l=t[1].dataType,c=!(he.areEqual(i,n)&&he.areEqual(n,u)),h=i,y=he.size(i);if(c){let $=hi.calcShape(hi.calcShape(i,n,!1),u,!1);if(!$)throw new Error("Can't perform where op on the given tensors");h=$,y=he.size(h)}let b=Math.ceil(y/4);return{name:"Where",shaderCache:{inputDependencies:["rank","rank","rank"]},getShaderSource:$=>lm($,t,h,c,l),getRunData:()=>({outputs:[{dims:h,dataType:l}],dispatchGroup:{x:Math.ceil(y/64/4)},programUniforms:[{type:12,data:b},...ie(u,i,n,h)]})}},pm=t=>{t.compute(dm(t.inputs))}}),cm,s2=m(()=>{Ko(),ls(),Zo(),Qo(),pl(),q(),kt(),Cv(),Ov(),Rv(),Bv(),Mv(),Dv(),Nv(),Pv(),Uv(),Lv(),qv(),Vv(),Wv(),Gv(),Fv(),Hv(),jv(),Kv(),xh(),Zv(),Qv(),Xv(),Yv(),Jv(),ss(),e2(),Rh(),t2(),r2(),i2(),Ch(),a2(),or(),uo(),n2(),cm=new Map([["Abs",[js]],["Acos",[Ks]],["Acosh",[Zs]],["Add",[Eu]],["ArgMax",[Os,us]],["ArgMin",[As,us]],["Asin",[Qs]],["Asinh",[Xs]],["Atan",[Ys]],["Atanh",[Js]],["Attention",[Ps]],["AveragePool",[df,lf]],["BatchNormalization",[Vs]],["BiasAdd",[Fs]],["BiasSplitGelu",[Su]],["Cast",[to,eo]],["Ceil",[ao]],["Clip",[io]],["Concat",[Le,pt]],["Conv",[xl,$l]],["ConvTranspose",[vc,wc]],["Cos",[_i]],["Cosh",[Xo]],["CumSum",[Sc,Tc]],["DepthToSpace",[zc,Cc]],["DequantizeLinear",[wf,bf]],["Div",[Iu]],["Einsum",[Dc,Nc]],["Elu",[Yo,ma]],["Equal",[zu]],["Erf",[Jo]],["Exp",[eu]],["Expand",[qc]],["FastGelu",[Wc]],["Floor",[tu]],["FusedConv",[xl,$l]],["Gather",[jc,Hc]],["GatherElements",[ah,ih]],["GatherBlockQuantized",[Jc,eh]],["GatherND",[Zc,Qc]],["Gelu",[ru]],["Gemm",[uh,oh]],["GlobalAveragePool",[cf,pf]],["GlobalMaxPool",[gf,mf]],["Greater",[f]],["GreaterOrEqual",[E]],["GridSample",[yh,_h]],["GroupQueryAttention",[Nh]],["HardSigmoid",[du,lu]],["InstanceNormalization",[Lh]],["LayerNormalization",[Wh]],["LeakyRelu",[iu,ma]],["Less",[w]],["LessOrEqual",[S]],["Log",[_u]],["MatMul",[Fh]],["MatMulNBits",[Zh,Qh]],["MaxPool",[hf,ff]],["Mul",[Tn]],["MultiHeadAttention",[vh,bh]],["Neg",[nu]],["Not",[au]],["Pad",[sf]],["Pow",[lo]],["QuickGelu",[$u,ma]],["Range",[xf]],["Reciprocal",[su]],["ReduceMin",[Es]],["ReduceMean",[gt]],["ReduceMax",[as]],["ReduceSum",[zs]],["ReduceProd",[Is]],["ReduceL1",[is]],["ReduceL2",[yt]],["ReduceLogSum",[Cs]],["ReduceLogSumExp",[ks]],["ReduceSumSquare",[ns]],["Relu",[ou]],["Resize",[Gf,Ff]],["RotaryEmbedding",[Oh]],["ScatterND",[Ef,kf]],["Sigmoid",[uu]],["Sin",[pu]],["Sinh",[cu]],["Slice",[Jf,em]],["SkipLayerNormalization",[Kf]],["Split",[Ih,zh]],["Sqrt",[hu]],["Softmax",[im,am]],["Sub",[po]],["Tan",[fu]],["Tanh",[mu]],["ThresholdedRelu",[yu,ma]],["Tile",[um]],["Transpose",[Ga,wn]],["Where",[pm]]])}),hm,o2=m(()=>{Kt(),Mr(),Ke(),hm=class{constructor(t){this.backend=t,this.repo=new Map,this.attributesBound=!1}getArtifact(t){return this.repo.get(t)}setArtifact(t,i){this.repo.set(t,i)}run(t,i,n,u,l){He(t.programInfo.name);let c=this.backend.device,h=this.backend.getComputePassEncoder();this.backend.writeTimestamp(this.backend.pendingDispatchNumber*2);let y=[];for(let $ of i)y.push({binding:y.length,resource:{buffer:$.buffer}});for(let $ of n)y.push({binding:y.length,resource:{buffer:$.buffer}});l&&y.push({binding:y.length,resource:l});let b=c.createBindGroup({layout:t.computePipeline.getBindGroupLayout(0),entries:y,label:t.programInfo.name});if(this.backend.sessionStatus==="capturing"){let $={kernelId:this.backend.currentKernelId,computePipeline:t.computePipeline,bindGroup:b,dispatchGroup:u};this.backend.capturedCommandList.get(this.backend.currentSessionId).push($)}h.setPipeline(t.computePipeline),h.setBindGroup(0,b),h.dispatchWorkgroups(...u),this.backend.writeTimestamp(this.backend.pendingDispatchNumber*2+1),this.backend.pendingDispatchNumber++,(this.backend.pendingDispatchNumber>=this.backend.maxDispatchNumber||this.backend.queryType==="at-passes")&&this.backend.endComputePass(),this.backend.pendingDispatchNumber>=this.backend.maxDispatchNumber&&this.backend.flush(),Rt(t.programInfo.name)}dispose(){}build(t,i){He(t.name);let n=this.backend.device,u=[];[{feature:"shader-f16",extension:"f16"},{feature:"subgroups",extension:"subgroups"}].forEach($=>{n.features.has($.feature)&&u.push(`enable ${$.extension};`)});let l=Mt(i,this.backend.device.limits),c=t.getShaderSource(l),h=`${u.join(`
`)}
${l.additionalImplementations}
${c}`,y=n.createShaderModule({code:h,label:t.name});mt("verbose",()=>`[WebGPU] ${t.name} shader code: ${h}`);let b=n.createComputePipeline({compute:{module:y,entryPoint:"main"},layout:"auto",label:t.name});return Rt(t.name),{programInfo:t,computePipeline:b,uniformVariablesInfo:l.variablesInfo}}normalizeDispatchGroupSize(t){let i=typeof t=="number"?t:t.x,n=typeof t=="number"?1:t.y||1,u=typeof t=="number"?1:t.z||1,l=this.backend.device.limits.maxComputeWorkgroupsPerDimension;if(i<=l&&n<=l&&u<=l)return[i,n,u];let c=i*n*u,h=Math.ceil(Math.sqrt(c));if(h>l){if(h=Math.ceil(Math.cbrt(c)),h>l)throw new Error("Total dispatch size exceeds WebGPU maximum.");return[h,h,h]}else return[h,h,1]}}}),fm={};_(fm,{WebGpuBackend:()=>_m});var mm,gm,ym,_m,u2=m(()=>{Kt(),it(),Mr(),Jr(),ws(),s2(),o2(),mm=(t,i)=>{if(i.length!==t.length)throw new Error(`inputDependencies length ${i.length} is not equal to inputTensors length ${t.length}.`);let n=[];for(let u=0;u<t.length;++u){let l=t[u].dataType;switch(i[u]){case"none":{n.push("");break}case"type":{n.push(`${l}`);break}case"rank":{let c=t[u].dims.length;n.push(`${l};${c}`);break}case"dims":{let c=t[u].dims.join(",");n.push(`${l};${c}`);break}default:throw new Error(`unsupported input dependency: ${i[u]}`)}}return n.join("|")},gm=(t,i,n)=>{var l,c;let u=t.name;return(l=t.shaderCache)!=null&&l.hint&&(u+="["+t.shaderCache.hint+"]"),u+=":"+n+`:${mm(i,((c=t.shaderCache)==null?void 0:c.inputDependencies)??new Array(i.length).fill("dims"))}`,u},ym=class{constructor(t){t&&(this.architecture=t.architecture,this.vendor=t.vendor)}isArchitecture(t){return this.architecture===t}isVendor(t){return this.vendor===t}},_m=class{constructor(){this.currentSessionId=null,this.currentKernelId=null,this.commandEncoder=null,this.computePassEncoder=null,this.maxDispatchNumber=16,this.pendingDispatchNumber=0,this.pendingKernels=[],this.pendingQueries=new Map,this.sessionStatus="default",this.capturedCommandList=new Map,this.capturedPendingKernels=new Map,this.sessionExternalDataMapping=new Map}get currentKernelCustomData(){if(this.currentKernelId===null)throw new Error("currentKernelCustomData(): currentKernelId is null. (should not happen)");let t=this.kernelCustomData.get(this.currentKernelId);return t||(t={},this.kernelCustomData.set(this.currentKernelId,t)),t}async initialize(t,i){this.env=t;let n=[],u={requiredLimits:{maxComputeWorkgroupStorageSize:i.limits.maxComputeWorkgroupStorageSize,maxComputeWorkgroupsPerDimension:i.limits.maxComputeWorkgroupsPerDimension,maxStorageBufferBindingSize:i.limits.maxStorageBufferBindingSize,maxBufferSize:i.limits.maxBufferSize,maxComputeInvocationsPerWorkgroup:i.limits.maxComputeInvocationsPerWorkgroup,maxComputeWorkgroupSizeX:i.limits.maxComputeWorkgroupSizeX,maxComputeWorkgroupSizeY:i.limits.maxComputeWorkgroupSizeY,maxComputeWorkgroupSizeZ:i.limits.maxComputeWorkgroupSizeZ},requiredFeatures:n},l=c=>i.features.has(c)&&n.push(c)&&!0;l("chromium-experimental-timestamp-query-inside-passes")||l("timestamp-query"),l("shader-f16"),l("subgroups"),this.device=await i.requestDevice(u),this.adapterInfo=new ym(i.info||await i.requestAdapterInfo()),this.gpuDataManager=ha(this),this.programManager=new hm(this),this.kernels=new Map,this.kernelPersistentData=new Map,this.kernelCustomData=new Map,oa(t.logLevel,!!t.debug),this.device.onuncapturederror=c=>{c.error instanceof GPUValidationError&&console.error(`An uncaught WebGPU validation error was raised: ${c.error.message}`)},Object.defineProperty(this.env.webgpu,"device",{value:this.device,writable:!1,enumerable:!0,configurable:!0}),Object.defineProperty(this.env.webgpu,"adapter",{value:i,writable:!1,enumerable:!0,configurable:!1}),this.setQueryType()}dispose(){var t;typeof this.querySet<"u"&&this.querySet.destroy(),this.gpuDataManager.dispose(),this.device&&((t=this.env)!=null&&t.webgpu)&&this.device.lost.then(()=>{delete this.env.webgpu.device})}getCommandEncoder(){return this.commandEncoder||(this.commandEncoder=this.device.createCommandEncoder()),this.commandEncoder}getComputePassEncoder(){if(!this.computePassEncoder){let t=this.getCommandEncoder(),i={};this.queryType==="at-passes"&&(i.timestampWrites={querySet:this.querySet,beginningOfPassWriteIndex:this.pendingDispatchNumber*2,endOfPassWriteIndex:this.pendingDispatchNumber*2+1}),this.computePassEncoder=t.beginComputePass(i)}return this.computePassEncoder}endComputePass(){this.computePassEncoder&&(this.computePassEncoder.end(),this.computePassEncoder=null)}flush(){if(!this.commandEncoder)return;He(),this.endComputePass();let t;this.queryType!=="none"&&(this.commandEncoder.resolveQuerySet(this.querySet,0,this.pendingDispatchNumber*2,this.queryResolveBuffer,0),t=this.device.createBuffer({size:this.pendingDispatchNumber*2*8,usage:GPUBufferUsage.MAP_READ|GPUBufferUsage.COPY_DST}),this.pendingQueries.set(t,this.pendingKernels),this.pendingKernels=[],this.commandEncoder.copyBufferToBuffer(this.queryResolveBuffer,0,t,0,this.pendingDispatchNumber*2*8)),this.device.queue.submit([this.commandEncoder.finish()]),this.gpuDataManager.refreshPendingBuffers(),this.commandEncoder=null,this.pendingDispatchNumber=0,this.queryType!=="none"&&t.mapAsync(GPUMapMode.READ).then(()=>{var u;let i=new BigUint64Array(t.getMappedRange()),n=this.pendingQueries.get(t);for(let l=0;l<i.length/2;l++){let c=n[l],h=c.kernelId,y=this.kernels.get(h),b=y.kernelType,$=y.kernelName,k=c.programName,I=c.inputTensorViews,O=c.outputTensorViews,M=i[l*2],D=i[l*2+1];typeof this.queryTimeBase>"u"&&(this.queryTimeBase=M);let L=Number(M-this.queryTimeBase),Z=Number(D-this.queryTimeBase);if(!Number.isSafeInteger(L)||!Number.isSafeInteger(Z))throw new RangeError("incorrect timestamp range");if((u=this.env.webgpu.profiling)!=null&&u.ondata)this.env.webgpu.profiling.ondata({version:1,inputsMetadata:I.map(W=>({dims:W.dims,dataType:rr(W.dataType)})),outputsMetadata:O.map(W=>({dims:W.dims,dataType:rr(W.dataType)})),kernelId:h,kernelType:b,kernelName:$,programName:k,startTime:L,endTime:Z});else{let W="";I.forEach((J,Y)=>{W+=`input[${Y}]: [${J.dims}] | ${rr(J.dataType)}, `});let V="";O.forEach((J,Y)=>{V+=`output[${Y}]: [${J.dims}] | ${rr(J.dataType)}, `}),console.log(`[profiling] kernel "${h}|${b}|${$}|${k}" ${W}${V}start time: ${L} ns, execution time: ${Z-L} ns`)}hr("GPU",`${k}::${M}::${D}`)}t.unmap(),this.pendingQueries.delete(t)}),Rt()}run(t,i,n,u,l,c){He(t.name);let h=[];for(let V=0;V<i.length;++V){let J=i[V].data;if(J===0)continue;let Y=this.gpuDataManager.get(J);if(!Y)throw new Error(`no GPU data for input: ${J}`);h.push(Y)}let{outputs:y,dispatchGroup:b,programUniforms:$}=t.getRunData(i),k=n.length===0?y.map((V,J)=>J):n;if(k.length!==y.length)throw new Error(`Output size ${k.length} must be equal to ${y.length}.`);let I=[],O=[];for(let V=0;V<y.length;++V){if(!Number.isInteger(k[V])||k[V]<-3||k[V]>=c)throw new Error(`Invalid output index: ${k[V]}`);if(k[V]===-3)continue;let J=k[V]===-1,Y=k[V]===-2,se=J||Y?l(y[V].dataType,y[V].dims):u(k[V],y[V].dataType,y[V].dims);if(I.push(se),se.data===0)continue;let de=this.gpuDataManager.get(se.data);if(!de)throw new Error(`no GPU data for output: ${se.data}`);if(J&&this.temporaryData.push(de),Y){let fe=this.kernelPersistentData.get(this.currentKernelId);fe||(fe=[],this.kernelPersistentData.set(this.currentKernelId,fe)),fe.push(de)}O.push(de)}if(h.length!==i.length||O.length!==I.length){if(O.length===0)return Rt(t.name),I;throw new Error(`Program ${t.name} has zero-sized tensor(s) in inputs or outputs. This is not supported now.`)}let M;if($){let V=0,J=[];$.forEach(fe=>{let we=typeof fe.data=="number"?[fe.data]:fe.data;if(we.length===0)return;let xe=fe.type===10?2:4,De,at;fe.type===10?(at=we.length>4?16:we.length>2?8:we.length*xe,De=we.length>4?16:xe*we.length):(at=we.length<=2?we.length*xe:16,De=16),V=Math.ceil(V/at)*at,J.push(V);let et=fe.type===10?8:4;V+=we.length>4?Math.ceil(we.length/et)*De:we.length*xe});let Y=16;V=Math.ceil(V/Y)*Y;let se=new ArrayBuffer(V);$.forEach((fe,we)=>{let xe=J[we],De=typeof fe.data=="number"?[fe.data]:fe.data;if(fe.type===6)new Int32Array(se,xe,De.length).set(De);else if(fe.type===12)new Uint32Array(se,xe,De.length).set(De);else if(fe.type===10)new Uint16Array(se,xe,De.length).set(De);else if(fe.type===1)new Float32Array(se,xe,De.length).set(De);else throw new Error(`Unsupported uniform type: ${rr(fe.type)}`)});let de=this.gpuDataManager.create(V,GPUBufferUsage.COPY_DST|GPUBufferUsage.UNIFORM);this.device.queue.writeBuffer(de.buffer,0,se,0,V),this.gpuDataManager.release(de.id),M={offset:0,size:V,buffer:de.buffer}}let D=this.programManager.normalizeDispatchGroupSize(b),L=D[1]===1&&D[2]===1,Z=gm(t,i,L),W=this.programManager.getArtifact(Z);if(W||(W=this.programManager.build(t,D),this.programManager.setArtifact(Z,W),mt("info",()=>`[artifact] key: ${Z}, programName: ${t.name}`)),$&&W.uniformVariablesInfo){if($.length!==W.uniformVariablesInfo.length)throw new Error(`Uniform variables count mismatch: expect ${W.uniformVariablesInfo.length}, got ${$.length} in program "${W.programInfo.name}".`);for(let V=0;V<$.length;V++){let J=$[V],Y=J.type,se=typeof J.data=="number"?1:J.data.length,[de,fe]=W.uniformVariablesInfo[V];if(Y!==de||se!==fe)throw new Error(`Uniform variable ${V} mismatch: expect type ${de} with size ${fe}, got type ${Y} with size ${se} in program "${W.programInfo.name}".`)}}if(mt("info",()=>`[ProgramManager] run "${t.name}" (key=${Z}) with ${D[0]}x${D[1]}x${D[2]}`),this.queryType!=="none"||this.sessionStatus==="capturing"){let V={kernelId:this.currentKernelId,programName:W.programInfo.name,inputTensorViews:i,outputTensorViews:I};this.pendingKernels.push(V),this.sessionStatus==="capturing"&&this.capturedPendingKernels.get(this.currentSessionId).push(V)}return this.programManager.run(W,h,O,D,M),Rt(t.name),I}upload(t,i){this.gpuDataManager.upload(t,i)}memcpy(t,i){this.gpuDataManager.memcpy(t,i)}async download(t,i){await this.gpuDataManager.download(t,i)}alloc(t){return this.gpuDataManager.create(t).id}free(t){return this.gpuDataManager.release(t)}createKernel(t,i,n,u){let l=cm.get(t);if(!l)throw new Error(`kernel not implemented: ${t}`);let c={kernelType:t,kernelName:u,kernelEntry:l[0],attributes:[l[1],n]};this.kernels.set(i,c)}releaseKernel(t){let i=this.kernelPersistentData.get(t);if(i){for(let n of i)this.gpuDataManager.release(n.id);this.kernelPersistentData.delete(t)}this.kernelCustomData.delete(t),this.kernels.delete(t)}computeKernel(t,i,n){let u=this.kernels.get(t);if(!u)throw new Error(`kernel not created: ${t}`);let l=u.kernelType,c=u.kernelName,h=u.kernelEntry,y=u.attributes;if(this.currentKernelId!==null)throw new Error(`kernel "[${l}] ${c}" is not allowed to be called recursively`);this.currentKernelId=t,y[0]&&(y[1]=y[0](y[1]),y[0]=void 0),mt("info",()=>`[WebGPU] Start to run kernel "[${l}] ${c}"...`);let b=this.env.debug;this.temporaryData=[];try{return b&&this.device.pushErrorScope("validation"),h(i,y[1]),0}catch($){return n.push(Promise.resolve(`[WebGPU] Kernel "[${l}] ${c}" failed. ${$}`)),1}finally{b&&n.push(this.device.popErrorScope().then($=>$?`GPU validation error for kernel "[${l}] ${c}": ${$.message}`:null));for(let $ of this.temporaryData)this.gpuDataManager.release($.id);this.temporaryData=[],this.currentKernelId=null}}registerBuffer(t,i,n,u){let l=this.sessionExternalDataMapping.get(t);l||(l=new Map,this.sessionExternalDataMapping.set(t,l));let c=l.get(i),h=this.gpuDataManager.registerExternalBuffer(n,u,c);return l.set(i,[h,n]),h}unregisterBuffers(t){let i=this.sessionExternalDataMapping.get(t);i&&(i.forEach(n=>this.gpuDataManager.unregisterExternalBuffer(n[0])),this.sessionExternalDataMapping.delete(t))}getBuffer(t){let i=this.gpuDataManager.get(t);if(!i)throw new Error(`no GPU data for buffer: ${t}`);return i.buffer}createDownloader(t,i,n){return async()=>{let u=await yn(this,t,i);return jr(u.buffer,n)}}writeTimestamp(t){this.queryType==="inside-passes"&&this.computePassEncoder.writeTimestamp(this.querySet,t)}setQueryType(){var t;this.queryType="none",(((t=this.env.webgpu.profiling)==null?void 0:t.mode)==="default"||(typeof this.env.trace>"u"?this.env.wasm.trace:this.env.trace))&&(this.device.features.has("chromium-experimental-timestamp-query-inside-passes")?this.queryType="inside-passes":this.device.features.has("timestamp-query")&&(this.queryType="at-passes"),this.queryType!=="none"&&typeof this.querySet>"u"&&(this.querySet=this.device.createQuerySet({type:"timestamp",count:this.maxDispatchNumber*2}),this.queryResolveBuffer=this.device.createBuffer({size:this.maxDispatchNumber*2*8,usage:GPUBufferUsage.COPY_SRC|GPUBufferUsage.QUERY_RESOLVE})))}captureBegin(){mt("info","captureBegin"),this.capturedCommandList.get(this.currentSessionId)||this.capturedCommandList.set(this.currentSessionId,[]),this.capturedPendingKernels.get(this.currentSessionId)||this.capturedPendingKernels.set(this.currentSessionId,[]),this.flush(),this.sessionStatus="capturing"}captureEnd(){mt("info","captureEnd"),this.flush(),this.sessionStatus="default"}replay(){mt("info","replay"),this.sessionStatus="replaying";let t=this.capturedCommandList.get(this.currentSessionId),i=this.capturedPendingKernels.get(this.currentSessionId),n=t.length;this.pendingKernels=[];for(let u=0;u<n;u++){let l=this.getComputePassEncoder(),c=t[u];this.writeTimestamp(this.pendingDispatchNumber*2),l.setPipeline(c.computePipeline),l.setBindGroup(0,c.bindGroup),l.dispatchWorkgroups(...c.dispatchGroup),this.writeTimestamp(this.pendingDispatchNumber*2+1),this.pendingDispatchNumber++,this.queryType!=="none"&&this.pendingKernels.push(i[u]),(this.pendingDispatchNumber>=this.maxDispatchNumber||this.queryType==="at-passes")&&this.endComputePass(),this.pendingDispatchNumber>=this.maxDispatchNumber&&this.flush()}this.flush(),this.sessionStatus="default"}onCreateSession(){this.gpuDataManager.onCreateSession()}onReleaseSession(t){this.unregisterBuffers(t),this.capturedCommandList.has(t)&&this.capturedCommandList.delete(t),this.capturedPendingKernels.has(t)&&this.capturedPendingKernels.delete(t),this.gpuDataManager.onReleaseSession(t)}onRunStart(t){this.currentSessionId=t,this.setQueryType()}}}),wm={};_(wm,{init:()=>$m});var Mu,bm,$m,l2=m(()=>{it(),Mr(),Xe(),gn(),Mu=class Pw{constructor(i,n,u,l){this.module=i,this.dataType=n,this.data=u,this.dims=l}getFloat32Array(){if(this.dataType!==1)throw new Error("Invalid data type");let i=he.size(this.dims);return i===0?new Float32Array:new Float32Array(this.module.HEAP8.buffer,this.data,i)}getBigInt64Array(){if(this.dataType!==7)throw new Error("Invalid data type");let i=he.size(this.dims);return i===0?new BigInt64Array:new BigInt64Array(this.module.HEAP8.buffer,this.data,i)}getInt32Array(){if(this.dataType!==6)throw new Error("Invalid data type");let i=he.size(this.dims);return i===0?new Int32Array:new Int32Array(this.module.HEAP8.buffer,this.data,i)}getUint16Array(){if(this.dataType!==10&&this.dataType!==4)throw new Error("Invalid data type");let i=he.size(this.dims);return i===0?new Uint16Array:new Uint16Array(this.module.HEAP8.buffer,this.data,i)}reshape(i){if(he.size(i)!==he.size(this.dims))throw new Error("Invalid new shape");return new Pw(this.module,this.dataType,this.data,i)}},bm=class{constructor(t,i,n){this.module=t,this.backend=i,this.customDataOffset=0,this.customDataSize=0,this.adapterInfo=i.adapterInfo;let u=t.PTR_SIZE,l=n/t.PTR_SIZE,c=u===4?"i32":"i64";this.opKernelContext=Number(t.getValue(u*l++,c));let h=Number(t.getValue(u*l++,c));this.outputCount=Number(t.getValue(u*l++,c)),this.customDataOffset=Number(t.getValue(u*l++,"*")),this.customDataSize=Number(t.getValue(u*l++,c));let y=[];for(let b=0;b<h;b++){let $=Number(t.getValue(u*l++,c)),k=Number(t.getValue(u*l++,"*")),I=Number(t.getValue(u*l++,c)),O=[];for(let M=0;M<I;M++)O.push(Number(t.getValue(u*l++,c)));y.push(new Mu(t,$,k,O))}this.inputs=y}get kernelCustomData(){return this.backend.currentKernelCustomData}get customDataBuffer(){return this.module.HEAPU8.subarray(this.customDataOffset,this.customDataOffset+this.customDataSize)}compute(t,i){var h;let n=((h=i==null?void 0:i.inputs)==null?void 0:h.map(y=>typeof y=="number"?this.inputs[y]:y))??this.inputs,u=(i==null?void 0:i.outputs)??[],l=(y,b,$)=>new Mu(this.module,b,this.output(y,$),$),c=(y,b)=>{let $=wr(y,b);if(!$)throw new Error(`Unsupported data type: ${y}`);let k=$>0?this.backend.gpuDataManager.create($).id:0;return new Mu(this.module,y,k,b)};return this.backend.run(t,n,u,l,c,this.outputCount)}output(t,i){let n=this.module.stackSave();try{let u=this.module.PTR_SIZE,l=u===4?"i32":"i64",c=this.module.stackAlloc((1+i.length)*u);this.module.setValue(c,i.length,l);for(let h=0;h<i.length;h++)this.module.setValue(c+u*(h+1),i[h],l);return this.module._JsepOutput(this.opKernelContext,t,c)}catch(u){throw new Error(`Failed to generate kernel's output[${t}] with dims [${i}]. If you are running with pre-allocated output, please make sure the output type/dims are correct. Error: ${u}`)}finally{this.module.stackRestore(n)}}},$m=async(t,i,n,u)=>{let l=i.jsepInit;if(!l)throw new Error("Failed to initialize JSEP. The WebAssembly module is not built with JSEP support.");if(t==="webgpu"){let c=(u2(),x(fm)).WebGpuBackend,h=new c;await h.initialize(n,u),l("webgpu",[h,y=>h.alloc(Number(y)),y=>h.free(y),(y,b,$,k=!1)=>{if(k)mt("verbose",()=>`[WebGPU] jsepCopyGpuToGpu: src=${Number(y)}, dst=${Number(b)}, size=${Number($)}`),h.memcpy(Number(y),Number(b));else{mt("verbose",()=>`[WebGPU] jsepCopyCpuToGpu: dataOffset=${Number(y)}, gpuDataId=${Number(b)}, size=${Number($)}`);let I=i.HEAPU8.subarray(Number(y>>>0),Number(y>>>0)+Number($));h.upload(Number(b),I)}},async(y,b,$)=>{mt("verbose",()=>`[WebGPU] jsepCopyGpuToCpu: gpuDataId=${y}, dataOffset=${b}, size=${$}`),await h.download(Number(y),()=>i.HEAPU8.subarray(Number(b)>>>0,Number(b+$)>>>0))},(y,b,$)=>h.createKernel(y,Number(b),$,i.UTF8ToString(i._JsepGetNodeName(Number(b)))),y=>h.releaseKernel(y),(y,b,$,k)=>{mt("verbose",()=>`[WebGPU] jsepRun: sessionHandle=${$}, kernel=${y}, contextDataOffset=${b}`);let I=new bm(i,h,Number(b));return h.computeKernel(Number(y),I,k)},()=>h.captureBegin(),()=>h.captureEnd(),()=>h.replay()])}else{let c=new La(n);l("webnn",[c,()=>c.reserveTensorId(),h=>c.releaseTensorId(h),async(h,y,b,$,k)=>c.ensureTensor(h,y,b,$,k),(h,y)=>{c.uploadTensor(h,y)},async(h,y)=>c.downloadTensor(h,y),(h,y)=>c.registerMLContext(h,y),!!n.trace])}}}),vm,Fl,Hl,Qa,xm,jl,Du,Kl,Zl,Ql,Xl,Yl,Jl,Sm=m(()=>{Kt(),Hr(),_s(),it(),kr(),Gi(),hn(),vm=(t,i)=>{st()._OrtInit(t,i)!==0&&Ye("Can't initialize onnxruntime.")},Fl=async t=>{vm(t.wasm.numThreads,fr(t.logLevel))},Hl=async(t,i)=>{var u,l;(l=(u=st()).asyncInit)==null||l.call(u);let n=t.webgpu.adapter;if(i==="webgpu"){if(typeof navigator>"u"||!navigator.gpu)throw new Error("WebGPU is not supported in current environment");if(n){if(typeof n.limits!="object"||typeof n.features!="object"||typeof n.requestDevice!="function")throw new Error("Invalid GPU adapter set in `env.webgpu.adapter`. It must be a GPUAdapter object.")}else{let c=t.webgpu.powerPreference;if(c!==void 0&&c!=="low-power"&&c!=="high-performance")throw new Error(`Invalid powerPreference setting: "${c}"`);let h=t.webgpu.forceFallbackAdapter;if(h!==void 0&&typeof h!="boolean")throw new Error(`Invalid forceFallbackAdapter setting: "${h}"`);if(n=await navigator.gpu.requestAdapter({powerPreference:c,forceFallbackAdapter:h}),!n)throw new Error('Failed to get GPU adapter. You may need to enable flag "--enable-unsafe-webgpu" if you are using Chrome.')}}if(i==="webnn"&&(typeof navigator>"u"||!navigator.ml))throw new Error("WebNN is not supported in current environment");{let c=(l2(),x(wm)).init;i==="webgpu"&&await c("webgpu",st(),t,n),i==="webnn"&&await c("webnn",st(),t)}},Qa=new Map,xm=t=>{let i=st(),n=i.stackSave();try{let u=i.PTR_SIZE,l=i.stackAlloc(2*u);i._OrtGetInputOutputCount(t,l,l+u)!==0&&Ye("Can't get session input/output count.");let c=u===4?"i32":"i64";return[Number(i.getValue(l,c)),Number(i.getValue(l+u,c))]}finally{i.stackRestore(n)}},jl=(t,i)=>{let n=st(),u=n.stackSave(),l=0;try{let c=n.PTR_SIZE,h=n.stackAlloc(2*c);n._OrtGetInputOutputMetadata(t,i,h,h+c)!==0&&Ye("Can't get session input/output metadata.");let y=Number(n.getValue(h,"*"));l=Number(n.getValue(h+c,"*"));let b=n.HEAP32[l/4];if(b===0)return[y,0];let $=n.HEAPU32[l/4+1],k=[];for(let I=0;I<$;I++){let O=Number(n.getValue(l+8+I*c,"*"));k.push(O!==0?n.UTF8ToString(O):Number(n.getValue(l+8+(I+$)*c,"*")))}return[y,b,k]}finally{n.stackRestore(u),l!==0&&n._OrtFree(l)}},Du=t=>{let i=st(),n=i._malloc(t.byteLength);if(n===0)throw new Error(`Can't create a session. failed to allocate a buffer of size ${t.byteLength}.`);return i.HEAPU8.set(t,n),[n,t.byteLength]},Kl=async(t,i)=>{var I,O,M,D;let n,u,l=st();Array.isArray(t)?[n,u]=t:t.buffer===l.HEAPU8.buffer?[n,u]=[t.byteOffset,t.byteLength]:[n,u]=Du(t);let c=0,h=0,y=0,b=[],$=[],k=[];try{if([h,b]=await cn(i),(i==null?void 0:i.externalData)&&l.mountExternalData){let we=[];for(let xe of i.externalData){let De=typeof xe=="string"?xe:xe.path;we.push(sa(typeof xe=="string"?xe:xe.data).then(at=>{l.mountExternalData(De,at)}))}await Promise.all(we)}for(let we of(i==null?void 0:i.executionProviders)??[])if((typeof we=="string"?we:we.name)==="webnn"){if(l.shouldTransferToMLTensor=!1,typeof we!="string"){let xe=we,De=xe==null?void 0:xe.context,at=xe==null?void 0:xe.gpuDevice,et=xe==null?void 0:xe.deviceType,tt=xe==null?void 0:xe.powerPreference;De?l.currentContext=De:at?l.currentContext=await l.webnnCreateMLContext(at):l.currentContext=await l.webnnCreateMLContext({deviceType:et,powerPreference:tt})}else l.currentContext=await l.webnnCreateMLContext();break}c=await l._OrtCreateSession(n,u,h),(I=l.webgpuOnCreateSession)==null||I.call(l,c),c===0&&Ye("Can't create a session."),(O=l.jsepOnCreateSession)==null||O.call(l),l.currentContext&&(l.webnnRegisterMLContext(c,l.currentContext),l.currentContext=void 0,l.shouldTransferToMLTensor=!0);let[L,Z]=xm(c),W=!!(i!=null&&i.enableGraphCapture),V=[],J=[],Y=[],se=[],de=[];for(let we=0;we<L;we++){let[xe,De,at]=jl(c,we);xe===0&&Ye("Can't get an input name."),$.push(xe);let et=l.UTF8ToString(xe);V.push(et),Y.push(De===0?{name:et,isTensor:!1}:{name:et,isTensor:!0,type:rr(De),shape:at})}for(let we=0;we<Z;we++){let[xe,De,at]=jl(c,we+L);xe===0&&Ye("Can't get an output name."),k.push(xe);let et=l.UTF8ToString(xe);J.push(et),se.push(De===0?{name:et,isTensor:!1}:{name:et,isTensor:!0,type:rr(De),shape:at});{if(W&&(i==null?void 0:i.preferredOutputLocation)===void 0){de.push("gpu-buffer");continue}let tt=typeof(i==null?void 0:i.preferredOutputLocation)=="string"?i.preferredOutputLocation:((M=i==null?void 0:i.preferredOutputLocation)==null?void 0:M[et])??"cpu",xt=l.webnnIsGraphOutput;if(tt==="cpu"&&xt&&xt(c,et)){de.push("ml-tensor-cpu-output");continue}if(tt!=="cpu"&&tt!=="cpu-pinned"&&tt!=="gpu-buffer"&&tt!=="ml-tensor")throw new Error(`Not supported preferred output location: ${tt}.`);if(W&&tt!=="gpu-buffer")throw new Error(`Not supported preferred output location: ${tt}. Only 'gpu-buffer' location is supported when enableGraphCapture is true.`);de.push(tt)}}let fe=null;return de.some(we=>we==="gpu-buffer"||we==="ml-tensor"||we==="ml-tensor-cpu-output")&&(y=l._OrtCreateBinding(c),y===0&&Ye("Can't create IO binding."),fe={handle:y,outputPreferredLocations:de,outputPreferredLocationsEncoded:de.map(we=>we==="ml-tensor-cpu-output"?"ml-tensor":we).map(we=>Ra(we))}),Qa.set(c,[c,$,k,fe,W,!1]),[c,V,J,Y,se]}catch(L){throw $.forEach(Z=>l._OrtFree(Z)),k.forEach(Z=>l._OrtFree(Z)),y!==0&&l._OrtReleaseBinding(y)!==0&&Ye("Can't release IO binding."),c!==0&&l._OrtReleaseSession(c)!==0&&Ye("Can't release session."),L}finally{l._free(n),h!==0&&l._OrtReleaseSessionOptions(h)!==0&&Ye("Can't release session options."),b.forEach(L=>l._free(L)),(D=l.unmountExternalData)==null||D.call(l)}},Zl=t=>{var b,$,k;let i=st(),n=Qa.get(t);if(!n)throw new Error(`cannot release session. invalid session id: ${t}`);let[u,l,c,h,y]=n;h&&(y&&i._OrtClearBoundOutputs(h.handle)!==0&&Ye("Can't clear bound outputs."),i._OrtReleaseBinding(h.handle)!==0&&Ye("Can't release IO binding.")),(b=i.jsepOnReleaseSession)==null||b.call(i,t),($=i.webnnOnReleaseSession)==null||$.call(i,t),(k=i.webgpuOnReleaseSession)==null||k.call(i,t),l.forEach(I=>i._OrtFree(I)),c.forEach(I=>i._OrtFree(I)),i._OrtReleaseSession(u)!==0&&Ye("Can't release session."),Qa.delete(t)},Ql=async(t,i,n,u,l,c,h=!1)=>{if(!t){i.push(0);return}let y=st(),b=y.PTR_SIZE,$=t[0],k=t[1],I=t[3],O=I,M,D;if($==="string"&&(I==="gpu-buffer"||I==="ml-tensor"))throw new Error("String tensor is not supported on GPU.");if(h&&I!=="gpu-buffer")throw new Error(`External buffer must be provided for input/output index ${c} when enableGraphCapture is true.`);if(I==="gpu-buffer"){let W=t[2].gpuBuffer;D=wr(Er($),k);{let V=y.jsepRegisterBuffer;if(!V)throw new Error('Tensor location "gpu-buffer" is not supported without using WebGPU.');M=V(u,c,W,D)}}else if(I==="ml-tensor"){let W=t[2].mlTensor;D=wr(Er($),k);let V=y.webnnRegisterMLTensor;if(!V)throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');M=V(u,W,Er($),k)}else{let W=t[2];if(Array.isArray(W)){D=b*W.length,M=y._malloc(D),n.push(M);for(let V=0;V<W.length;V++){if(typeof W[V]!="string")throw new TypeError(`tensor data at index ${V} is not a string`);y.setValue(M+V*b,Ht(W[V],n),"*")}}else{let V=y.webnnIsGraphInput,J=y.webnnIsGraphOutput;if($!=="string"&&V&&J){let Y=y.UTF8ToString(l);if(V(u,Y)||J(u,Y)){let se=Er($);D=wr(se,k),O="ml-tensor";let de=y.webnnCreateTemporaryTensor,fe=y.webnnUploadTensor;if(!de||!fe)throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');let we=await de(u,se,k);fe(we,new Uint8Array(W.buffer,W.byteOffset,W.byteLength)),M=we}else D=W.byteLength,M=y._malloc(D),n.push(M),y.HEAPU8.set(new Uint8Array(W.buffer,W.byteOffset,D),M)}else D=W.byteLength,M=y._malloc(D),n.push(M),y.HEAPU8.set(new Uint8Array(W.buffer,W.byteOffset,D),M)}}let L=y.stackSave(),Z=y.stackAlloc(4*k.length);try{k.forEach((V,J)=>y.setValue(Z+J*b,V,b===4?"i32":"i64"));let W=y._OrtCreateTensor(Er($),M,D,Z,k.length,Ra(O));W===0&&Ye(`Can't create tensor for input/output. session=${u}, index=${c}.`),i.push(W)}finally{y.stackRestore(L)}},Xl=async(t,i,n,u,l,c)=>{var et,tt,xt,zt;let h=st(),y=h.PTR_SIZE,b=Qa.get(t);if(!b)throw new Error(`cannot run inference. invalid session id: ${t}`);let $=b[0],k=b[1],I=b[2],O=b[3],M=b[4],D=b[5],L=i.length,Z=u.length,W=0,V=[],J=[],Y=[],se=[],de=[],fe=h.stackSave(),we=h.stackAlloc(L*y),xe=h.stackAlloc(L*y),De=h.stackAlloc(Z*y),at=h.stackAlloc(Z*y);try{[W,V]=pn(c),sr("wasm prepareInputOutputTensor");for(let Ne=0;Ne<L;Ne++)await Ql(n[Ne],J,se,t,k[i[Ne]],i[Ne],M);for(let Ne=0;Ne<Z;Ne++)await Ql(l[Ne],Y,se,t,I[u[Ne]],L+u[Ne],M);tr("wasm prepareInputOutputTensor");for(let Ne=0;Ne<L;Ne++)h.setValue(we+Ne*y,J[Ne],"*"),h.setValue(xe+Ne*y,k[i[Ne]],"*");for(let Ne=0;Ne<Z;Ne++)h.setValue(De+Ne*y,Y[Ne],"*"),h.setValue(at+Ne*y,I[u[Ne]],"*");if(O&&!D){let{handle:Ne,outputPreferredLocations:Ot,outputPreferredLocationsEncoded:Ae}=O;if(k.length!==L)throw new Error(`input count from feeds (${L}) is expected to be always equal to model's input count (${k.length}).`);sr("wasm bindInputsOutputs");for(let Ge=0;Ge<L;Ge++){let Ze=i[Ge];await h._OrtBindInput(Ne,k[Ze],J[Ge])!==0&&Ye(`Can't bind input[${Ge}] for session=${t}.`)}for(let Ge=0;Ge<Z;Ge++){let Ze=u[Ge];(et=l[Ge])!=null&&et[3]?(de.push(Y[Ge]),h._OrtBindOutput(Ne,I[Ze],Y[Ge],0)!==0&&Ye(`Can't bind pre-allocated output[${Ge}] for session=${t}.`)):h._OrtBindOutput(Ne,I[Ze],0,Ae[Ze])!==0&&Ye(`Can't bind output[${Ge}] to ${Ot[Ge]} for session=${t}.`)}tr("wasm bindInputsOutputs"),Qa.set(t,[$,k,I,O,M,!0])}(tt=h.jsepOnRunStart)==null||tt.call(h,$),(xt=h.webnnOnRunStart)==null||xt.call(h,$);let rt;O?rt=await h._OrtRunWithBinding($,O.handle,Z,De,W):rt=await h._OrtRun($,xe,we,L,at,Z,De,W),rt!==0&&Ye("failed to call OrtRun().");let ot=[],ur=[];sr("wasm ProcessOutputTensor");for(let Ne=0;Ne<Z;Ne++){let Ot=Number(h.getValue(De+Ne*y,"*"));if(Ot===Y[Ne]||de.includes(Y[Ne])){ot.push(l[Ne]),Ot!==Y[Ne]&&h._OrtReleaseTensor(Ot)!==0&&Ye("Can't release tensor.");continue}let Ae=h.stackSave(),Ge=h.stackAlloc(4*y),Ze=!1,Pe,Vt=0;try{h._OrtGetTensorData(Ot,Ge,Ge+y,Ge+2*y,Ge+3*y)!==0&&Ye(`Can't access output tensor data on index ${Ne}.`);let _a=y===4?"i32":"i64",Ir=Number(h.getValue(Ge,_a));Vt=h.getValue(Ge+y,"*");let zi=h.getValue(Ge+y*2,"*"),Ci=Number(h.getValue(Ge+y*3,_a)),Ya=[];for(let xr=0;xr<Ci;xr++)Ya.push(Number(h.getValue(zi+xr*y,_a)));h._OrtFree(zi)!==0&&Ye("Can't free memory for tensor dims.");let Ja=Ya.reduce((xr,lr)=>xr*lr,1);Pe=rr(Ir);let bo=O==null?void 0:O.outputPreferredLocations[u[Ne]];if(Pe==="string"){if(bo==="gpu-buffer"||bo==="ml-tensor")throw new Error("String tensor is not supported on GPU.");let xr=[];for(let lr=0;lr<Ja;lr++){let wa=h.getValue(Vt+lr*y,"*"),f2=h.getValue(Vt+(lr+1)*y,"*"),m2=lr===Ja-1?void 0:f2-wa;xr.push(h.UTF8ToString(wa,m2))}ot.push([Pe,Ya,xr,"cpu"])}else if(bo==="gpu-buffer"&&Ja>0){let xr=h.jsepGetBuffer;if(!xr)throw new Error('preferredLocation "gpu-buffer" is not supported without using WebGPU.');let lr=xr(Vt),wa=wr(Ir,Ja);if(wa===void 0||!xi(Pe))throw new Error(`Unsupported data type: ${Pe}`);Ze=!0,ot.push([Pe,Ya,{gpuBuffer:lr,download:h.jsepCreateDownloader(lr,wa,Pe),dispose:()=>{h._OrtReleaseTensor(Ot)!==0&&Ye("Can't release tensor.")}},"gpu-buffer"])}else if(bo==="ml-tensor"&&Ja>0){let xr=h.webnnEnsureTensor,lr=h.webnnIsGraphInputOutputTypeSupported;if(!xr||!lr)throw new Error('preferredLocation "ml-tensor" is not supported without using WebNN.');if(wr(Ir,Ja)===void 0||!na(Pe))throw new Error(`Unsupported data type: ${Pe}`);if(!lr(t,Pe,!1))throw new Error(`preferredLocation "ml-tensor" for ${Pe} output is not supported by current WebNN Context.`);let wa=await xr(t,Vt,Ir,Ya,!1);Ze=!0,ot.push([Pe,Ya,{mlTensor:wa,download:h.webnnCreateMLTensorDownloader(Vt,Pe),dispose:()=>{h.webnnReleaseTensorId(Vt),h._OrtReleaseTensor(Ot)}},"ml-tensor"])}else if(bo==="ml-tensor-cpu-output"&&Ja>0){let xr=h.webnnCreateMLTensorDownloader(Vt,Pe)(),lr=ot.length;Ze=!0,ur.push((async()=>{let wa=[lr,await xr];return h.webnnReleaseTensorId(Vt),h._OrtReleaseTensor(Ot),wa})()),ot.push([Pe,Ya,[],"cpu"])}else{let xr=pr(Pe),lr=new xr(Ja);new Uint8Array(lr.buffer,lr.byteOffset,lr.byteLength).set(h.HEAPU8.subarray(Vt,Vt+lr.byteLength)),ot.push([Pe,Ya,lr,"cpu"])}}finally{h.stackRestore(Ae),Pe==="string"&&Vt&&h._free(Vt),Ze||h._OrtReleaseTensor(Ot)}}O&&!M&&(h._OrtClearBoundOutputs(O.handle)!==0&&Ye("Can't clear bound outputs."),Qa.set(t,[$,k,I,O,M,!1]));for(let[Ne,Ot]of await Promise.all(ur))ot[Ne][2]=Ot;return tr("wasm ProcessOutputTensor"),ot}finally{(zt=h.webnnOnRunEnd)==null||zt.call(h,$),h.stackRestore(fe),J.forEach(rt=>h._OrtReleaseTensor(rt)),Y.forEach(rt=>h._OrtReleaseTensor(rt)),se.forEach(rt=>h._free(rt)),W!==0&&h._OrtReleaseRunOptions(W),V.forEach(rt=>h._free(rt))}},Yl=t=>{let i=st(),n=Qa.get(t);if(!n)throw new Error("invalid session id");let u=n[0],l=i._OrtEndProfiling(u);l===0&&Ye("Can't get an profile file name."),i._OrtFree(l)},Jl=t=>{let i=[];for(let n of t){let u=n[2];!Array.isArray(u)&&"buffer"in u&&i.push(u.buffer)}return i}}),Xa,Xr,ps,_o,wo,Nu,ed,Pu,zn,Cn,Tm,km,Em,Im,zm,Cm,Am,Om,Rm=m(()=>{Kt(),Sm(),kr(),qi(),Xa=()=>!!B.wasm.proxy&&typeof document<"u",ps=!1,_o=!1,wo=!1,Pu=new Map,zn=(t,i)=>{let n=Pu.get(t);n?n.push(i):Pu.set(t,[i])},Cn=()=>{if(ps||!_o||wo||!Xr)throw new Error("worker not ready")},Tm=t=>{switch(t.data.type){case"init-wasm":ps=!1,t.data.err?(wo=!0,ed[1](t.data.err)):(_o=!0,ed[0]()),Nu&&(URL.revokeObjectURL(Nu),Nu=void 0);break;case"init-ep":case"copy-from":case"create":case"release":case"run":case"end-profiling":{let i=Pu.get(t.data.type);t.data.err?i.shift()[1](t.data.err):i.shift()[0](t.data.out);break}}},km=async()=>{if(!_o){if(ps)throw new Error("multiple calls to 'initWasm()' detected.");if(wo)throw new Error("previous call to 'initWasm()' failed.");if(ps=!0,Xa())return new Promise((t,i)=>{Xr==null||Xr.terminate(),dn().then(([n,u])=>{try{Xr=u,Xr.onerror=c=>i(c),Xr.onmessage=Tm,ed=[t,i];let l={type:"init-wasm",in:B};if(!l.in.wasm.wasmPaths&&n){let c=ta();c&&(l.in.wasm.wasmPaths=c)}Xr.postMessage(l),Nu=n}catch(l){i(l)}},i)});try{await Wi(B.wasm),await Fl(B),_o=!0}catch(t){throw wo=!0,t}finally{ps=!1}}},Em=async t=>{if(Xa())return Cn(),new Promise((i,n)=>{zn("init-ep",[i,n]);let u={type:"init-ep",in:{epName:t,env:B}};Xr.postMessage(u)});await Hl(B,t)},Im=async t=>Xa()?(Cn(),new Promise((i,n)=>{zn("copy-from",[i,n]);let u={type:"copy-from",in:{buffer:t}};Xr.postMessage(u,[t.buffer])})):Du(t),zm=async(t,i)=>{if(Xa()){if(i!=null&&i.preferredOutputLocation)throw new Error('session option "preferredOutputLocation" is not supported for proxy.');return Cn(),new Promise((n,u)=>{zn("create",[n,u]);let l={type:"create",in:{model:t,options:{...i}}},c=[];t instanceof Uint8Array&&c.push(t.buffer),Xr.postMessage(l,c)})}else return Kl(t,i)},Cm=async t=>{if(Xa())return Cn(),new Promise((i,n)=>{zn("release",[i,n]);let u={type:"release",in:t};Xr.postMessage(u)});Zl(t)},Am=async(t,i,n,u,l,c)=>{if(Xa()){if(n.some(h=>h[3]!=="cpu"))throw new Error("input tensor on GPU is not supported for proxy.");if(l.some(h=>h))throw new Error("pre-allocated output tensor is not supported for proxy.");return Cn(),new Promise((h,y)=>{zn("run",[h,y]);let b=n,$={type:"run",in:{sessionId:t,inputIndices:i,inputs:b,outputIndices:u,options:c}};Xr.postMessage($,Jl(b))})}else return Xl(t,i,n,u,l,c)},Om=async t=>{if(Xa())return Cn(),new Promise((i,n)=>{zn("end-profiling",[i,n]);let u={type:"end-profiling",in:t};Xr.postMessage(u)});Yl(t)}}),td,Bm,Mm,d2=m(()=>{Kt(),Rm(),it(),ui(),hn(),td=(t,i)=>{switch(t.location){case"cpu":return[t.type,t.dims,t.data,"cpu"];case"gpu-buffer":return[t.type,t.dims,{gpuBuffer:t.gpuBuffer},"gpu-buffer"];case"ml-tensor":return[t.type,t.dims,{mlTensor:t.mlTensor},"ml-tensor"];default:throw new Error(`invalid data location: ${t.location} for ${i()}`)}},Bm=t=>{switch(t[3]){case"cpu":return new St(t[0],t[2],t[1]);case"gpu-buffer":{let i=t[0];if(!xi(i))throw new Error(`not supported data type: ${i} for deserializing GPU tensor`);let{gpuBuffer:n,download:u,dispose:l}=t[2];return St.fromGpuBuffer(n,{dataType:i,dims:t[1],download:u,dispose:l})}case"ml-tensor":{let i=t[0];if(!na(i))throw new Error(`not supported data type: ${i} for deserializing MLTensor tensor`);let{mlTensor:n,download:u,dispose:l}=t[2];return St.fromMLTensor(n,{dataType:i,dims:t[1],download:u,dispose:l})}default:throw new Error(`invalid data location: ${t[3]}`)}},Mm=class{async fetchModelAndCopyToWasmMemory(t){return Im(await sa(t))}async loadModel(t,i){He();let n;typeof t=="string"?n=await this.fetchModelAndCopyToWasmMemory(t):n=t,[this.sessionId,this.inputNames,this.outputNames,this.inputMetadata,this.outputMetadata]=await zm(n,i),Rt()}async dispose(){return Cm(this.sessionId)}async run(t,i,n){He();let u=[],l=[];Object.entries(t).forEach(I=>{let O=I[0],M=I[1],D=this.inputNames.indexOf(O);if(D===-1)throw new Error(`invalid input '${O}'`);u.push(M),l.push(D)});let c=[],h=[];Object.entries(i).forEach(I=>{let O=I[0],M=I[1],D=this.outputNames.indexOf(O);if(D===-1)throw new Error(`invalid output '${O}'`);c.push(M),h.push(D)});let y=u.map((I,O)=>td(I,()=>`input "${this.inputNames[l[O]]}"`)),b=c.map((I,O)=>I?td(I,()=>`output "${this.outputNames[h[O]]}"`):null),$=await Am(this.sessionId,l,y,h,b,n),k={};for(let I=0;I<$.length;I++)k[this.outputNames[h[I]]]=c[I]??Bm($[I]);return Rt(),k}startProfiling(){}endProfiling(){Om(this.sessionId)}}}),Dm={};_(Dm,{OnnxruntimeWebAssemblyBackend:()=>id,initializeFlags:()=>rd,wasmBackend:()=>Nm});var rd,id,Nm,p2=m(()=>{Kt(),Rm(),d2(),rd=()=>{(typeof B.wasm.initTimeout!="number"||B.wasm.initTimeout<0)&&(B.wasm.initTimeout=0);let t=B.wasm.simd;if(typeof t!="boolean"&&t!==void 0&&t!=="fixed"&&t!=="relaxed"&&(console.warn(`Property "env.wasm.simd" is set to unknown value "${t}". Reset it to \`false\` and ignore SIMD feature checking.`),B.wasm.simd=!1),typeof B.wasm.proxy!="boolean"&&(B.wasm.proxy=!1),typeof B.wasm.trace!="boolean"&&(B.wasm.trace=!1),typeof B.wasm.numThreads!="number"||!Number.isInteger(B.wasm.numThreads)||B.wasm.numThreads<=0)if(typeof self<"u"&&!self.crossOriginIsolated)B.wasm.numThreads=1;else{let i=typeof navigator>"u"?g("node:os").cpus().length:navigator.hardwareConcurrency;B.wasm.numThreads=Math.min(4,Math.ceil((i||1)/2))}},id=class{async init(t){rd(),await km(),await Em(t)}async createInferenceSessionHandler(t,i){let n=new Mm;return await n.loadModel(t,i),n}},Nm=new id}),Pm={};_(Pm,{InferenceSession:()=>Rr,TRACE:()=>hr,TRACE_EVENT_BEGIN:()=>sr,TRACE_EVENT_END:()=>tr,TRACE_FUNC_BEGIN:()=>He,TRACE_FUNC_END:()=>Rt,Tensor:()=>St,default:()=>h2,env:()=>B,registerBackend:()=>A}),Kt(),Kt(),Kt();var c2="1.26.0",h2=sn;{let t=(p2(),x(Dm)).wasmBackend;A("webgpu",t,5),A("webnn",t,5),A("cpu",t,10),A("wasm",t,10)}return Object.defineProperty(B.versions,"web",{value:c2,enumerable:!0}),x(Pm)})();/**
 * @license
 * Copyright 2021 Google LLC. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * =============================================================================
 *//**
 * @license
 * Copyright 2020 Google LLC. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * =============================================================================
 *//**
 * @license
 * Copyright 2019 Google LLC. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * =============================================================================
 */e.exports=a})(nd)),nd.exports}var An={},sd={},jm;function b2(){return jm||(jm=1,Object.defineProperty(sd,"__esModule",{value:!0})),sd}var To={},Km;function $2(){if(Km)return To;Km=1;var e;Object.defineProperty(To,"__esModule",{value:!0}),To.SileroLegacy=void 0;const r=Ho();class a{constructor(o,p,d,g,m){this.ortInstance=o,this._session=p,this._h=d,this._c=g,this._sr=m,this.reset_state=()=>{const _=Array(128).fill(0);this._h=new this.ortInstance.Tensor("float32",_,[2,1,64]),this._c=new this.ortInstance.Tensor("float32",_,[2,1,64])},this.process=async _=>{var R;const x={input:new this.ortInstance.Tensor("float32",_,[1,_.length]),h:this._h,c:this._c,sr:this._sr},T=await this._session.run(x);this._h=T.hn,this._c=T.cn;const[C]=(R=T.output)==null?void 0:R.data;return{notSpeech:1-C,isSpeech:C}},this.release=async()=>{await this._session.release(),this._h.dispose(),this._c.dispose(),this._sr.dispose()}}}return To.SileroLegacy=a,e=a,a.new=async(s,o)=>{r.log.debug("initializing vad");const p=await o(),d=await s.InferenceSession.create(p),g=new s.Tensor("int64",[16000n]),m=Array(128).fill(0),_=new s.Tensor("float32",m,[2,1,64]),v=new s.Tensor("float32",m,[2,1,64]);return r.log.debug("vad is initialized"),new e(s,d,_,v,g)},To}var ko={},Zm;function v2(){if(Zm)return ko;Zm=1;var e;Object.defineProperty(ko,"__esModule",{value:!0}),ko.SileroV5=void 0;const r=Ho();function a(o){const p=Array(256).fill(0);return new o.Tensor("float32",p,[2,1,128])}class s{constructor(p,d,g,m){this._session=p,this._state=d,this._sr=g,this.ortInstance=m,this.reset_state=()=>{this._state=a(this.ortInstance)},this.process=async _=>{var R;const x={input:new this.ortInstance.Tensor("float32",_,[1,_.length]),state:this._state,sr:this._sr},T=await this._session.run(x);if(!T.stateN)throw new Error("No state from model");if(this._state=T.stateN,!((R=T.output)!=null&&R.data))throw new Error("No output from model");const C=T.output.data[0];if(typeof C!="number")throw new Error("Weird output data");return{notSpeech:1-C,isSpeech:C}},this.release=async()=>{await this._session.release(),this._state.dispose(),this._sr.dispose()}}}return ko.SileroV5=s,e=s,s.new=async(o,p)=>{r.log.debug("Loading VAD...");const d=await p(),g=await o.InferenceSession.create(d),m=new o.Tensor("int64",[16000n]),_=a(o);return r.log.debug("...finished loading VAD"),new e(g,_,m,o)},ko}var Qm;function Uw(){return Qm||(Qm=1,(function(e){var r=An&&An.__createBinding||(Object.create?(function(p,d,g,m){m===void 0&&(m=g);var _=Object.getOwnPropertyDescriptor(d,g);(!_||("get"in _?!d.__esModule:_.writable||_.configurable))&&(_={enumerable:!0,get:function(){return d[g]}}),Object.defineProperty(p,m,_)}):(function(p,d,g,m){m===void 0&&(m=g),p[m]=d[g]})),a=An&&An.__exportStar||function(p,d){for(var g in p)g!=="default"&&!Object.prototype.hasOwnProperty.call(d,g)&&r(d,p,g)};Object.defineProperty(e,"__esModule",{value:!0}),e.SileroV5=e.SileroLegacy=void 0,a(b2(),e);var s=$2();Object.defineProperty(e,"SileroLegacy",{enumerable:!0,get:function(){return s.SileroLegacy}});var o=v2();Object.defineProperty(e,"SileroV5",{enumerable:!0,get:function(){return o.SileroV5}})})(An)),An}var Eo={},Xm;function Lw(){if(Xm)return Eo;Xm=1,Object.defineProperty(Eo,"__esModule",{value:!0}),Eo.Resampler=void 0;const e=Ho();class r{constructor(s){this.options=s,this.process=o=>{const p=[];for(const d of o)for(this.inputBuffer.push(d);this.hasEnoughDataForFrame();){const g=this.generateOutputFrame();p.push(g)}return p},s.nativeSampleRate<16e3&&e.log.error("nativeSampleRate is too low. Should have 16000 = targetSampleRate <= nativeSampleRate"),this.inputBuffer=[]}async*stream(s){for(const o of s)for(this.inputBuffer.push(o);this.hasEnoughDataForFrame();)yield this.generateOutputFrame()}hasEnoughDataForFrame(){return this.inputBuffer.length*this.options.targetSampleRate/this.options.nativeSampleRate>=this.options.targetFrameSize}generateOutputFrame(){const s=new Float32Array(this.options.targetFrameSize);let o=0,p=0;for(;o<this.options.targetFrameSize;){let d=0,g=0;for(;p<Math.min(this.inputBuffer.length,(o+1)*this.options.nativeSampleRate/this.options.targetSampleRate);){const m=this.inputBuffer[p];m!==void 0&&(d+=m,g++),p++}s[o]=d/g,o++}return this.inputBuffer=this.inputBuffer.slice(p),s}}return Eo.Resampler=r,Eo}var Ym;function x2(){return Ym||(Ym=1,(function(e){var r=$a&&$a.__createBinding||(Object.create?(function(T,C,A,R){R===void 0&&(R=A);var H=Object.getOwnPropertyDescriptor(C,A);(!H||("get"in H?!C.__esModule:H.writable||H.configurable))&&(H={enumerable:!0,get:function(){return C[A]}}),Object.defineProperty(T,R,H)}):(function(T,C,A,R){R===void 0&&(R=A),T[R]=C[A]})),a=$a&&$a.__setModuleDefault||(Object.create?(function(T,C){Object.defineProperty(T,"default",{enumerable:!0,value:C})}):function(T,C){T.default=C}),s=$a&&$a.__importStar||function(T){if(T&&T.__esModule)return T;var C={};if(T!=null)for(var A in T)A!=="default"&&Object.prototype.hasOwnProperty.call(T,A)&&r(C,T,A);return a(C,T),C};Object.defineProperty(e,"__esModule",{value:!0}),e.NonRealTimeVAD=e.defaultNonRealTimeVADOptions=void 0;const o=s(w2()),p=Dw(),d=bp(),g=$p(),m=ll(),_=Uw(),v=Lw();e.defaultNonRealTimeVADOptions={...g.defaultFrameProcessorOptions,modelURL:p.baseAssetPath+"silero_vad_legacy.onnx",modelFetcher:d.defaultModelFetcher};class x{static async new(C={}){const A={...e.defaultNonRealTimeVADOptions,...C};(0,g.validateOptions)(A),A.ortConfig!==void 0&&A.ortConfig(o);const R=()=>A.modelFetcher(A.modelURL),H=await _.SileroLegacy.new(o,R),U=new g.FrameProcessor(H.process,H.reset_state,{positiveSpeechThreshold:A.positiveSpeechThreshold,negativeSpeechThreshold:A.negativeSpeechThreshold,redemptionMs:A.redemptionMs,preSpeechPadMs:A.preSpeechPadMs,minSpeechMs:A.minSpeechMs,submitUserSpeechOnPause:A.submitUserSpeechOnPause},1536/16);return U.resume(),new this(R,o,A,U)}constructor(C,A,R,H){this.modelFetcher=C,this.ort=A,this.options=R,this.frameProcessor=H,this.frameSamples=1536}async*run(C,A){const R={nativeSampleRate:A,targetSampleRate:16e3,targetFrameSize:this.frameSamples},H=new v.Resampler(R);let U=0,P=0,F=0;for await(const K of H.stream(C)){const ee=[];await this.frameProcessor.process(K,ae=>{ee.push(ae)});for(const ae of ee)switch(ae.msg){case m.Message.SpeechStart:U=F*this.frameSamples/16;break;case m.Message.SpeechEnd:P=(F+1)*this.frameSamples/16,yield{audio:ae.audio,start:U,end:P};break}F++}const G=[];this.frameProcessor.endSegment(K=>{G.push(K)});for(const K of G)switch(K.msg){case m.Message.SpeechEnd:yield{audio:K.audio,start:U,end:F*this.frameSamples/16}}}}e.NonRealTimeVAD=x})($a)),$a}var Ai={},Jm;function S2(){if(Jm)return Ai;Jm=1,Object.defineProperty(Ai,"__esModule",{value:!0}),Ai.audioFileToArray=Ai.encodeWAV=Ai.arrayBufferToBase64=Ai.minFramesForTargetMS=void 0;function e(g,m,_=16e3){return Math.ceil(g*_/1e3/m)}Ai.minFramesForTargetMS=e;function r(g){const m=new Uint8Array(g),_=m.byteLength,v=new Array(_);for(let x=0;x<_;x++){const T=m[x];if(T===void 0)break;v[x]=String.fromCharCode(T)}return btoa(v.join(""))}Ai.arrayBufferToBase64=r;function a(g,m=3,_=16e3,v=1,x=32){const T=x/8,C=v*T,A=new ArrayBuffer(44+g.length*T),R=new DataView(A);return p(R,0,"RIFF"),R.setUint32(4,36+g.length*T,!0),p(R,8,"WAVE"),p(R,12,"fmt "),R.setUint32(16,16,!0),R.setUint16(20,m,!0),R.setUint16(22,v,!0),R.setUint32(24,_,!0),R.setUint32(28,_*C,!0),R.setUint16(32,C,!0),R.setUint16(34,x,!0),p(R,36,"data"),R.setUint32(40,g.length*T,!0),m===1?o(R,44,g):s(R,44,g),A}Ai.encodeWAV=a;function s(g,m,_){for(let v=0;v<_.length;v++,m+=4)g.setFloat32(m,_[v],!0)}function o(g,m,_){for(let v=0;v<_.length;v++,m+=2){const x=Math.max(-1,Math.min(1,_[v]));g.setInt16(m,x<0?x*32768:x*32767,!0)}}function p(g,m,_){for(let v=0;v<_.length;v++)g.setUint8(m+v,_.charCodeAt(v))}async function d(g){const m=new OfflineAudioContext(1,1,44100),_=new FileReader;let v=null;if(await new Promise(C=>{_.addEventListener("loadend",()=>{const A=_.result;m.decodeAudioData(A,R=>{v=R,m.startRendering().then(()=>{console.log("Rendering completed successfully"),C()}).catch(H=>{console.error("Rendering failed: ",H)})},R=>{console.log("Error with decoding audio data: ",R)})}),_.readAsArrayBuffer(g)}),v===null)throw Error("some shit");const x=v,T=new Float32Array(x.length);for(let C=0;C<x.length;C++)for(let A=0;A<x.numberOfChannels;A++){const R=x.getChannelData(A)[C],H=T[C];if(R===void 0||H===void 0)throw new Error("sample or out[i] is undefined");T[C]=H+R}return{audio:T,sampleRate:x.sampleRate}}return Ai.audioFileToArray=d,Ai}var va={},od={exports:{}};/*!
 * ONNX Runtime Web v1.26.0
 * Copyright (c) Microsoft Corporation. All rights reserved.
 * Licensed under the MIT License.
 */var eg;function T2(){return eg||(eg=1,(function(e,r){var a=(()=>{var s=Object.defineProperty,o=Object.getOwnPropertyDescriptor,p=Object.getOwnPropertyNames,d=Object.prototype.hasOwnProperty,g=(z=>typeof wi<"u"?wi:typeof Proxy<"u"?new Proxy(z,{get:(N,j)=>(typeof wi<"u"?wi:N)[j]}):z)(function(z){if(typeof wi<"u")return wi.apply(this,arguments);throw Error('Dynamic require of "'+z+'" is not supported')}),m=(z,N)=>()=>(z&&(N=z(z=0)),N),_=(z,N)=>{for(var j in N)s(z,j,{get:N[j],enumerable:!0})},v=(z,N,j,te)=>{if(N&&typeof N=="object"||typeof N=="function")for(let X of p(N))!d.call(z,X)&&X!==j&&s(z,X,{get:()=>N[X],enumerable:!(te=o(N,X))||te.enumerable});return z},x=z=>v(s({},"__esModule",{value:!0}),z),T,C,A,R,H,U=m(()=>{T=new Map,C=[],A=(z,N,j)=>{if(N&&typeof N.init=="function"&&typeof N.createInferenceSessionHandler=="function"){let te=T.get(z);if(te===void 0)T.set(z,{backend:N,priority:j});else{if(te.priority>j)return;if(te.priority===j&&te.backend!==N)throw new Error(`cannot register backend "${z}" using priority ${j}`)}if(j>=0){let X=C.indexOf(z);X!==-1&&C.splice(X,1);for(let ue=0;ue<C.length;ue++)if(T.get(C[ue]).priority<=j){C.splice(ue,0,z);return}C.push(z)}return}throw new TypeError("not a valid backend")},R=async z=>{let N=T.get(z);if(!N)return"backend not found.";if(N.initialized)return N.backend;if(N.aborted)return N.error;{let j=!!N.initPromise;try{return j||(N.initPromise=N.backend.init(z)),await N.initPromise,N.initialized=!0,N.backend}catch(te){return j||(N.error=`${te}`,N.aborted=!0),N.error}finally{delete N.initPromise}}},H=async z=>{let N=z.executionProviders||[],j=N.map(le=>typeof le=="string"?le:le.name),te=j.length===0?C:j,X,ue=[],re=new Set;for(let le of te){let ve=await R(le);typeof ve=="string"?ue.push({name:le,err:ve}):(X||(X=ve),X===ve&&re.add(le))}if(!X)throw new Error(`no available backend found. ERR: ${ue.map(le=>`[${le.name}] ${le.err}`).join(", ")}`);for(let{name:le,err:ve}of ue)j.includes(le)&&console.warn(`removing requested execution provider "${le}" from session options because it is not available: ${ve}`);let ie=N.filter(le=>re.has(typeof le=="string"?le:le.name));return[X,new Proxy(z,{get:(le,ve)=>ve==="executionProviders"?ie:Reflect.get(le,ve)})]}}),P=m(()=>{U()}),F,G=m(()=>{F="1.26.0"}),K,ee,ae=m(()=>{G(),K="warning",ee={wasm:{},webgl:{},webgpu:{},versions:{common:F},set logLevel(z){if(z!==void 0){if(typeof z!="string"||["verbose","info","warning","error","fatal"].indexOf(z)===-1)throw new Error(`Unsupported logging level: ${z}`);K=z}},get logLevel(){return K}},Object.defineProperty(ee,"logLevel",{enumerable:!0})}),B,me=m(()=>{ae(),B=ee}),_e,Re,Ue=m(()=>{_e=(z,N)=>{let j=typeof document<"u"?document.createElement("canvas"):new OffscreenCanvas(1,1);j.width=z.dims[3],j.height=z.dims[2];let te=j.getContext("2d");if(te!=null){let X,ue;(N==null?void 0:N.tensorLayout)!==void 0&&N.tensorLayout==="NHWC"?(X=z.dims[2],ue=z.dims[3]):(X=z.dims[3],ue=z.dims[2]);let re=(N==null?void 0:N.format)!==void 0?N.format:"RGB",ie=N==null?void 0:N.norm,le,ve;ie===void 0||ie.mean===void 0?le=[255,255,255,255]:typeof ie.mean=="number"?le=[ie.mean,ie.mean,ie.mean,ie.mean]:(le=[ie.mean[0],ie.mean[1],ie.mean[2],0],ie.mean[3]!==void 0&&(le[3]=ie.mean[3])),ie===void 0||ie.bias===void 0?ve=[0,0,0,0]:typeof ie.bias=="number"?ve=[ie.bias,ie.bias,ie.bias,ie.bias]:(ve=[ie.bias[0],ie.bias[1],ie.bias[2],0],ie.bias[3]!==void 0&&(ve[3]=ie.bias[3]));let Se=ue*X,be=0,ce=Se,Fe=Se*2,oe=-1;re==="RGBA"?(be=0,ce=Se,Fe=Se*2,oe=Se*3):re==="RGB"?(be=0,ce=Se,Fe=Se*2):re==="RBG"&&(be=0,Fe=Se,ce=Se*2);for(let ke=0;ke<ue;ke++)for(let qt=0;qt<X;qt++){let dt=(z.data[be++]-ve[0])*le[0],lt=(z.data[ce++]-ve[1])*le[1],Mt=(z.data[Fe++]-ve[2])*le[2],Ke=oe===-1?255:(z.data[oe++]-ve[3])*le[3];te.fillStyle="rgba("+dt+","+lt+","+Mt+","+Ke+")",te.fillRect(qt,ke,1,1)}if("toDataURL"in j)return j.toDataURL();throw new Error("toDataURL is not supported")}else throw new Error("Can not access image data")},Re=(z,N)=>{let j=typeof document<"u"?document.createElement("canvas").getContext("2d"):new OffscreenCanvas(1,1).getContext("2d"),te;if(j!=null){let X,ue,re;(N==null?void 0:N.tensorLayout)!==void 0&&N.tensorLayout==="NHWC"?(X=z.dims[2],ue=z.dims[1],re=z.dims[3]):(X=z.dims[3],ue=z.dims[2],re=z.dims[1]);let ie=N!==void 0&&N.format!==void 0?N.format:"RGB",le=N==null?void 0:N.norm,ve,Se;le===void 0||le.mean===void 0?ve=[255,255,255,255]:typeof le.mean=="number"?ve=[le.mean,le.mean,le.mean,le.mean]:(ve=[le.mean[0],le.mean[1],le.mean[2],255],le.mean[3]!==void 0&&(ve[3]=le.mean[3])),le===void 0||le.bias===void 0?Se=[0,0,0,0]:typeof le.bias=="number"?Se=[le.bias,le.bias,le.bias,le.bias]:(Se=[le.bias[0],le.bias[1],le.bias[2],0],le.bias[3]!==void 0&&(Se[3]=le.bias[3]));let be=ue*X;if(N!==void 0&&(N.format!==void 0&&re===4&&N.format!=="RGBA"||re===3&&N.format!=="RGB"&&N.format!=="BGR"))throw new Error("Tensor format doesn't match input tensor dims");let ce=4,Fe=0,oe=1,ke=2,qt=3,dt=0,lt=be,Mt=be*2,Ke=-1;ie==="RGBA"?(dt=0,lt=be,Mt=be*2,Ke=be*3):ie==="RGB"?(dt=0,lt=be,Mt=be*2):ie==="RBG"&&(dt=0,Mt=be,lt=be*2),te=j.createImageData(X,ue);for(let Dt=0;Dt<ue*X;Fe+=ce,oe+=ce,ke+=ce,qt+=ce,Dt++)te.data[Fe]=(z.data[dt++]-Se[0])*ve[0],te.data[oe]=(z.data[lt++]-Se[1])*ve[1],te.data[ke]=(z.data[Mt++]-Se[2])*ve[2],te.data[qt]=Ke===-1?255:(z.data[Ke++]-Se[3])*ve[3]}else throw new Error("Can not access image data");return te}}),Me,pe,qe,Ve,ze,ht,Ce=m(()=>{er(),Me=(z,N)=>{if(z===void 0)throw new Error("Image buffer must be defined");if(N.height===void 0||N.width===void 0)throw new Error("Image height and width must be defined");if(N.tensorLayout==="NHWC")throw new Error("NHWC Tensor layout is not supported yet");let{height:j,width:te}=N,X=N.norm??{mean:255,bias:0},ue,re;typeof X.mean=="number"?ue=[X.mean,X.mean,X.mean,X.mean]:ue=[X.mean[0],X.mean[1],X.mean[2],X.mean[3]??255],typeof X.bias=="number"?re=[X.bias,X.bias,X.bias,X.bias]:re=[X.bias[0],X.bias[1],X.bias[2],X.bias[3]??0];let ie=N.format!==void 0?N.format:"RGBA",le=N.tensorFormat!==void 0&&N.tensorFormat!==void 0?N.tensorFormat:"RGB",ve=j*te,Se=le==="RGBA"?new Float32Array(ve*4):new Float32Array(ve*3),be=4,ce=0,Fe=1,oe=2,ke=3,qt=0,dt=ve,lt=ve*2,Mt=-1;ie==="RGB"&&(be=3,ce=0,Fe=1,oe=2,ke=-1),le==="RGBA"?Mt=ve*3:le==="RBG"?(qt=0,lt=ve,dt=ve*2):le==="BGR"&&(lt=0,dt=ve,qt=ve*2);for(let Ke=0;Ke<ve;Ke++,ce+=be,oe+=be,Fe+=be,ke+=be)Se[qt++]=(z[ce]+re[0])/ue[0],Se[dt++]=(z[Fe]+re[1])/ue[1],Se[lt++]=(z[oe]+re[2])/ue[2],Mt!==-1&&ke!==-1&&(Se[Mt++]=(z[ke]+re[3])/ue[3]);return le==="RGBA"?new _t("float32",Se,[1,4,j,te]):new _t("float32",Se,[1,3,j,te])},pe=async(z,N)=>{let j=typeof HTMLImageElement<"u"&&z instanceof HTMLImageElement,te=typeof ImageData<"u"&&z instanceof ImageData,X=typeof ImageBitmap<"u"&&z instanceof ImageBitmap,ue=typeof z=="string",re,ie=N??{},le=()=>{if(typeof document<"u")return document.createElement("canvas");if(typeof OffscreenCanvas<"u")return new OffscreenCanvas(1,1);throw new Error("Canvas is not supported")},ve=Se=>typeof HTMLCanvasElement<"u"&&Se instanceof HTMLCanvasElement||Se instanceof OffscreenCanvas?Se.getContext("2d"):null;if(j){let Se=le();Se.width=z.width,Se.height=z.height;let be=ve(Se);if(be!=null){let ce=z.height,Fe=z.width;if(N!==void 0&&N.resizedHeight!==void 0&&N.resizedWidth!==void 0&&(ce=N.resizedHeight,Fe=N.resizedWidth),N!==void 0){if(ie=N,N.tensorFormat!==void 0)throw new Error("Image input config format must be RGBA for HTMLImageElement");ie.tensorFormat="RGBA",ie.height=ce,ie.width=Fe}else ie.tensorFormat="RGBA",ie.height=ce,ie.width=Fe;be.drawImage(z,0,0),re=be.getImageData(0,0,Fe,ce).data}else throw new Error("Can not access image data")}else if(te){let Se,be;if(N!==void 0&&N.resizedWidth!==void 0&&N.resizedHeight!==void 0?(Se=N.resizedHeight,be=N.resizedWidth):(Se=z.height,be=z.width),N!==void 0&&(ie=N),ie.format="RGBA",ie.height=Se,ie.width=be,N!==void 0){let ce=le();ce.width=be,ce.height=Se;let Fe=ve(ce);if(Fe!=null)Fe.putImageData(z,0,0),re=Fe.getImageData(0,0,be,Se).data;else throw new Error("Can not access image data")}else re=z.data}else if(X){if(N===void 0)throw new Error("Please provide image config with format for Imagebitmap");let Se=le();Se.width=z.width,Se.height=z.height;let be=ve(Se);if(be!=null){let ce=z.height,Fe=z.width;return be.drawImage(z,0,0,Fe,ce),re=be.getImageData(0,0,Fe,ce).data,ie.height=ce,ie.width=Fe,Me(re,ie)}else throw new Error("Can not access image data")}else{if(ue)return new Promise((Se,be)=>{let ce=le(),Fe=ve(ce);if(!z||!Fe)return be();let oe=new Image;oe.crossOrigin="Anonymous",oe.src=z,oe.onload=()=>{ce.width=oe.width,ce.height=oe.height,Fe.drawImage(oe,0,0,ce.width,ce.height);let ke=Fe.getImageData(0,0,ce.width,ce.height);ie.height=ce.height,ie.width=ce.width,Se(Me(ke.data,ie))}});throw new Error("Input data provided is not supported - aborted tensor creation")}if(re!==void 0)return Me(re,ie);throw new Error("Input data provided is not supported - aborted tensor creation")},qe=(z,N)=>{let{width:j,height:te,download:X,dispose:ue}=N,re=[1,te,j,4];return new _t({location:"texture",type:"float32",texture:z,dims:re,download:X,dispose:ue})},Ve=(z,N)=>{let{dataType:j,dims:te,download:X,dispose:ue}=N;return new _t({location:"gpu-buffer",type:j??"float32",gpuBuffer:z,dims:te,download:X,dispose:ue})},ze=(z,N)=>{let{dataType:j,dims:te,download:X,dispose:ue}=N;return new _t({location:"ml-tensor",type:j??"float32",mlTensor:z,dims:te,download:X,dispose:ue})},ht=(z,N,j)=>new _t({location:"cpu-pinned",type:z,data:N,dims:j??[N.length]})}),nt,Te,Be,We,Ie=m(()=>{nt=new Map([["float32",Float32Array],["uint8",Uint8Array],["int8",Int8Array],["uint16",Uint16Array],["int16",Int16Array],["int32",Int32Array],["bool",Uint8Array],["float64",Float64Array],["uint32",Uint32Array],["int4",Uint8Array],["uint4",Uint8Array]]),Te=new Map([[Float32Array,"float32"],[Uint8Array,"uint8"],[Int8Array,"int8"],[Uint16Array,"uint16"],[Int16Array,"int16"],[Int32Array,"int32"],[Float64Array,"float64"],[Uint32Array,"uint32"]]),Be=!1,We=()=>{if(!Be){Be=!0;let z=typeof BigInt64Array<"u"&&BigInt64Array.from,N=typeof BigUint64Array<"u"&&BigUint64Array.from,j=globalThis.Float16Array,te=typeof j<"u"&&j.from;z&&(nt.set("int64",BigInt64Array),Te.set(BigInt64Array,"int64")),N&&(nt.set("uint64",BigUint64Array),Te.set(BigUint64Array,"uint64")),te?(nt.set("float16",j),Te.set(j,"float16")):nt.set("float16",Uint16Array)}}}),$t,_r,jt=m(()=>{er(),$t=z=>{let N=1;for(let j=0;j<z.length;j++){let te=z[j];if(typeof te!="number"||!Number.isSafeInteger(te))throw new TypeError(`dims[${j}] must be an integer, got: ${te}`);if(te<0)throw new RangeError(`dims[${j}] must be a non-negative integer, got: ${te}`);N*=te}return N},_r=(z,N)=>{switch(z.location){case"cpu":return new _t(z.type,z.data,N);case"cpu-pinned":return new _t({location:"cpu-pinned",data:z.data,type:z.type,dims:N});case"texture":return new _t({location:"texture",texture:z.texture,type:z.type,dims:N});case"gpu-buffer":return new _t({location:"gpu-buffer",gpuBuffer:z.gpuBuffer,type:z.type,dims:N});case"ml-tensor":return new _t({location:"ml-tensor",mlTensor:z.mlTensor,type:z.type,dims:N});default:throw new Error(`tensorReshape: tensor location ${z.location} is not supported`)}}}),_t,er=m(()=>{Ue(),Ce(),Ie(),jt(),_t=class{constructor(z,N,j){We();let te,X;if(typeof z=="object"&&"location"in z)switch(this.dataLocation=z.location,te=z.type,X=z.dims,z.location){case"cpu-pinned":{let re=nt.get(te);if(!re)throw new TypeError(`unsupported type "${te}" to create tensor from pinned buffer`);if(!(z.data instanceof re))throw new TypeError(`buffer should be of type ${re.name}`);this.cpuData=z.data;break}case"texture":{if(te!=="float32")throw new TypeError(`unsupported type "${te}" to create tensor from texture`);this.gpuTextureData=z.texture,this.downloader=z.download,this.disposer=z.dispose;break}case"gpu-buffer":{if(te!=="float32"&&te!=="float16"&&te!=="int32"&&te!=="int64"&&te!=="uint32"&&te!=="uint8"&&te!=="bool"&&te!=="uint4"&&te!=="int4")throw new TypeError(`unsupported type "${te}" to create tensor from gpu buffer`);this.gpuBufferData=z.gpuBuffer,this.downloader=z.download,this.disposer=z.dispose;break}case"ml-tensor":{if(te!=="float32"&&te!=="float16"&&te!=="int32"&&te!=="int64"&&te!=="uint32"&&te!=="uint64"&&te!=="int8"&&te!=="uint8"&&te!=="bool"&&te!=="uint4"&&te!=="int4")throw new TypeError(`unsupported type "${te}" to create tensor from MLTensor`);this.mlTensorData=z.mlTensor,this.downloader=z.download,this.disposer=z.dispose;break}default:throw new Error(`Tensor constructor: unsupported location '${this.dataLocation}'`)}else{let re,ie;if(typeof z=="string")if(te=z,ie=j,z==="string"){if(!Array.isArray(N))throw new TypeError("A string tensor's data must be a string array.");re=N}else{let le=nt.get(z);if(le===void 0)throw new TypeError(`Unsupported tensor type: ${z}.`);if(Array.isArray(N)){if(z==="float16"&&le===Uint16Array||z==="uint4"||z==="int4")throw new TypeError(`Creating a ${z} tensor from number array is not supported. Please use ${le.name} as data.`);z==="uint64"||z==="int64"?re=le.from(N,BigInt):re=le.from(N)}else if(N instanceof le)re=N;else if(N instanceof Uint8ClampedArray)if(z==="uint8")re=Uint8Array.from(N);else throw new TypeError("A Uint8ClampedArray tensor's data must be type of uint8");else if(z==="float16"&&N instanceof Uint16Array&&le!==Uint16Array)re=new globalThis.Float16Array(N.buffer,N.byteOffset,N.length);else throw new TypeError(`A ${te} tensor's data must be type of ${le}`)}else if(ie=N,Array.isArray(z)){if(z.length===0)throw new TypeError("Tensor type cannot be inferred from an empty array.");let le=typeof z[0];if(le==="string")te="string",re=z;else if(le==="boolean")te="bool",re=Uint8Array.from(z);else throw new TypeError(`Invalid element type of data array: ${le}.`)}else if(z instanceof Uint8ClampedArray)te="uint8",re=Uint8Array.from(z);else{let le=Te.get(z.constructor);if(le===void 0)throw new TypeError(`Unsupported type for tensor data: ${z.constructor}.`);te=le,re=z}if(ie===void 0)ie=[re.length];else if(!Array.isArray(ie))throw new TypeError("A tensor's dims must be a number array");X=ie,this.cpuData=re,this.dataLocation="cpu"}let ue=$t(X);if(this.cpuData&&ue!==this.cpuData.length&&!((te==="uint4"||te==="int4")&&Math.ceil(ue/2)===this.cpuData.length))throw new Error(`Tensor's size(${ue}) does not match data length(${this.cpuData.length}).`);this.type=te,this.dims=X,this.size=ue}static async fromImage(z,N){return pe(z,N)}static fromTexture(z,N){return qe(z,N)}static fromGpuBuffer(z,N){return Ve(z,N)}static fromMLTensor(z,N){return ze(z,N)}static fromPinnedBuffer(z,N,j){return ht(z,N,j)}toDataURL(z){return _e(this,z)}toImageData(z){return Re(this,z)}get data(){if(this.ensureValid(),!this.cpuData)throw new Error("The data is not on CPU. Use `getData()` to download GPU data to CPU, or use `texture` or `gpuBuffer` property to access the GPU data directly.");return this.cpuData}get location(){return this.dataLocation}get texture(){if(this.ensureValid(),!this.gpuTextureData)throw new Error("The data is not stored as a WebGL texture.");return this.gpuTextureData}get gpuBuffer(){if(this.ensureValid(),!this.gpuBufferData)throw new Error("The data is not stored as a WebGPU buffer.");return this.gpuBufferData}get mlTensor(){if(this.ensureValid(),!this.mlTensorData)throw new Error("The data is not stored as a WebNN MLTensor.");return this.mlTensorData}async getData(z){switch(this.ensureValid(),this.dataLocation){case"cpu":case"cpu-pinned":return this.data;case"texture":case"gpu-buffer":case"ml-tensor":{if(!this.downloader)throw new Error("The current tensor is not created with a specified data downloader.");if(this.isDownloading)throw new Error("The current tensor is being downloaded.");try{this.isDownloading=!0;let N=await this.downloader();return this.downloader=void 0,this.dataLocation="cpu",this.cpuData=N,z&&this.disposer&&(this.disposer(),this.disposer=void 0),N}finally{this.isDownloading=!1}}default:throw new Error(`cannot get data from location: ${this.dataLocation}`)}}dispose(){if(this.isDownloading)throw new Error("The current tensor is being downloaded.");this.disposer&&(this.disposer(),this.disposer=void 0),this.cpuData=void 0,this.gpuTextureData=void 0,this.gpuBufferData=void 0,this.mlTensorData=void 0,this.downloader=void 0,this.isDownloading=void 0,this.dataLocation="none"}ensureValid(){if(this.dataLocation==="none")throw new Error("The tensor is disposed.")}reshape(z){if(this.ensureValid(),this.downloader||this.disposer)throw new Error("Cannot reshape a tensor that owns GPU resource.");return _r(this,z)}}}),St,dr=m(()=>{er(),St=_t}),hr,Ct,He,Rt,sr,tr,Wr=m(()=>{ae(),hr=(z,N)=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||console.timeStamp(`${z}::ORT::${N}`)},Ct=(z,N)=>{var X;let j=((X=new Error().stack)==null?void 0:X.split(/\r\n|\r|\n/g))||[],te=!1;for(let ue=0;ue<j.length;ue++){if(te&&!j[ue].includes("TRACE_FUNC")){let re=`FUNC_${z}::${j[ue].trim().split(" ")[1]}`;N&&(re+=`::${N}`),hr("CPU",re);return}j[ue].includes("TRACE_FUNC")&&(te=!0)}},He=z=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||Ct("BEGIN",z)},Rt=z=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||Ct("END",z)},sr=z=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||console.time(`ORT::${z}`)},tr=z=>{(typeof ee.trace>"u"?!ee.wasm.trace:!ee.trace)||console.timeEnd(`ORT::${z}`)}}),$i,jn=m(()=>{U(),dr(),Wr(),$i=class qw{constructor(N){this.handler=N}async run(N,j,te){He(),sr("InferenceSession.run");let X={},ue={};if(typeof N!="object"||N===null||N instanceof St||Array.isArray(N))throw new TypeError("'feeds' must be an object that use input names as keys and OnnxValue as corresponding values.");let re=!0;if(typeof j=="object"){if(j===null)throw new TypeError("Unexpected argument[1]: cannot be null.");if(j instanceof St)throw new TypeError("'fetches' cannot be a Tensor");if(Array.isArray(j)){if(j.length===0)throw new TypeError("'fetches' cannot be an empty array.");re=!1;for(let ve of j){if(typeof ve!="string")throw new TypeError("'fetches' must be a string array or an object.");if(this.outputNames.indexOf(ve)===-1)throw new RangeError(`'fetches' contains invalid output name: ${ve}.`);X[ve]=null}if(typeof te=="object"&&te!==null)ue=te;else if(typeof te<"u")throw new TypeError("'options' must be an object.")}else{let ve=!1,Se=Object.getOwnPropertyNames(j);for(let be of this.outputNames)if(Se.indexOf(be)!==-1){let ce=j[be];(ce===null||ce instanceof St)&&(ve=!0,re=!1,X[be]=ce)}if(ve){if(typeof te=="object"&&te!==null)ue=te;else if(typeof te<"u")throw new TypeError("'options' must be an object.")}else ue=j}}else if(typeof j<"u")throw new TypeError("Unexpected argument[1]: must be 'fetches' or 'options'.");for(let ve of this.inputNames)if(typeof N[ve]>"u")throw new Error(`input '${ve}' is missing in 'feeds'.`);if(re)for(let ve of this.outputNames)X[ve]=null;let ie=await this.handler.run(N,X,ue),le={};for(let ve in ie)if(Object.hasOwnProperty.call(ie,ve)){let Se=ie[ve];Se instanceof St?le[ve]=Se:le[ve]=new St(Se.type,Se.data,Se.dims)}return tr("InferenceSession.run"),Rt(),le}async release(){return this.handler.dispose()}static async create(N,j,te,X){He(),sr("InferenceSession.create");let ue,re={};if(typeof N=="string"){if(ue=N,typeof j=="object"&&j!==null)re=j;else if(typeof j<"u")throw new TypeError("'options' must be an object.")}else if(N instanceof Uint8Array){if(ue=N,typeof j=="object"&&j!==null)re=j;else if(typeof j<"u")throw new TypeError("'options' must be an object.")}else if(N instanceof ArrayBuffer||typeof SharedArrayBuffer<"u"&&N instanceof SharedArrayBuffer){let Se=N,be=0,ce=N.byteLength;if(typeof j=="object"&&j!==null)re=j;else if(typeof j=="number"){if(be=j,!Number.isSafeInteger(be))throw new RangeError("'byteOffset' must be an integer.");if(be<0||be>=Se.byteLength)throw new RangeError(`'byteOffset' is out of range [0, ${Se.byteLength}).`);if(ce=N.byteLength-be,typeof te=="number"){if(ce=te,!Number.isSafeInteger(ce))throw new RangeError("'byteLength' must be an integer.");if(ce<=0||be+ce>Se.byteLength)throw new RangeError(`'byteLength' is out of range (0, ${Se.byteLength-be}].`);if(typeof X=="object"&&X!==null)re=X;else if(typeof X<"u")throw new TypeError("'options' must be an object.")}else if(typeof te<"u")throw new TypeError("'byteLength' must be a number.")}else if(typeof j<"u")throw new TypeError("'options' must be an object.");ue=new Uint8Array(Se,be,ce)}else throw new TypeError("Unexpected argument[0]: must be 'path' or 'buffer'.");let[ie,le]=await H(re),ve=await ie.createInferenceSessionHandler(ue,le);return tr("InferenceSession.create"),Rt(),new qw(ve)}startProfiling(){this.handler.startProfiling()}endProfiling(){this.handler.endProfiling()}get inputNames(){return this.handler.inputNames}get outputNames(){return this.handler.outputNames}get inputMetadata(){return this.handler.inputMetadata}get outputMetadata(){return this.handler.outputMetadata}}}),Rr,Kn=m(()=>{jn(),Rr=$i}),Zn=m(()=>{}),Qn=m(()=>{}),Xn=m(()=>{}),oi=m(()=>{}),sn={};_(sn,{InferenceSession:()=>Rr,TRACE:()=>hr,TRACE_EVENT_BEGIN:()=>sr,TRACE_EVENT_END:()=>tr,TRACE_FUNC_BEGIN:()=>He,TRACE_FUNC_END:()=>Rt,Tensor:()=>St,env:()=>B,registerBackend:()=>A});var Kt=m(()=>{P(),me(),Kn(),dr(),Zn(),Qn(),Wr(),Xn(),oi()}),ui=m(()=>{}),on={};_(on,{default:()=>ka});var Gr,Yi,ka,Yn=m(()=>{var z;mn(),kr(),qi(),Gr="ort-wasm-proxy-worker",Yi=((z=globalThis.self)==null?void 0:z.name)===Gr,Yi&&(self.onmessage=N=>{let{type:j,in:te}=N.data;try{switch(j){case"init-wasm":Wi(te.wasm).then(()=>{Br(te).then(()=>{postMessage({type:j})},X=>{postMessage({type:j,err:X})})},X=>{postMessage({type:j,err:X})});break;case"init-ep":{let{epName:X,env:ue}=te;Si(ue,X).then(()=>{postMessage({type:j})},re=>{postMessage({type:j,err:re})});break}case"copy-from":{let{buffer:X}=te,ue=mt(X);postMessage({type:j,out:ue});break}case"create":{let{model:X,options:ue}=te;Mr(X,ue).then(re=>{postMessage({type:j,out:re})},re=>{postMessage({type:j,err:re})});break}case"release":Ma(te),postMessage({type:j});break;case"run":{let{sessionId:X,inputIndices:ue,inputs:re,outputIndices:ie,options:le}=te;he(X,ue,re,ie,new Array(ie.length).fill(null),le).then(ve=>{ve.some(Se=>Se[3]!=="cpu")?postMessage({type:j,err:"Proxy does not support non-cpu tensor location."}):postMessage({type:j,out:ve},Da([...re,...ve]))},ve=>{postMessage({type:j,err:ve})});break}case"end-profiling":Fi(te),postMessage({type:j});break;default:}}catch(X){postMessage({type:j,err:X})}}),ka=Yi?null:N=>new Worker(N??Ut,{type:"classic",name:Gr})}),Ji,ea,Ut,ta,vi,un,ln,ra,Ea,Ui,dn,Li,Ia,qi=m(()=>{ui(),Ji=typeof location>"u"?void 0:location.origin,ea=()=>{var z,N;return typeof document<"u"?(z=document.currentScript)==null?void 0:z.src:typeof self<"u"?(N=self.location)==null?void 0:N.href:void 0},Ut=ea(),ta=()=>{if(Ut&&!Ut.startsWith("blob:"))return Ut.substring(0,Ut.lastIndexOf("/")+1)},vi=(z,N)=>{try{let j=N??Ut;return(j?new URL(z,j):new URL(z)).origin===Ji}catch{return!1}},un=(z,N)=>{let j=N??Ut;try{return(j?new URL(z,j):new URL(z)).href}catch{return}},ln=(z,N)=>`${N??"./"}${z}`,ra=async z=>{let N=await(await fetch(z,{credentials:"same-origin"})).blob();return URL.createObjectURL(N)},Ea=async z=>(await import(z)).default,Ui=(Yn(),x(on)).default,dn=async()=>{if(!Ut)throw new Error("Failed to load proxy worker: cannot determine the script source URL.");if(vi(Ut))return[void 0,Ui()];let z=await ra(Ut);return[z,Ui(z)]},Li=void 0,Ia=async(z,N,j,te)=>{let X=Li&&!(z||N);if(X)if(Ut)X=vi(Ut)||te&&!j;else if(te&&!j)X=!0;else throw new Error("cannot determine the script source URL.");if(X)return[void 0,Li];{let ue="ort-wasm-simd-threaded.mjs",re=z??un(ue,N),ie=j&&re&&!vi(re,N),le=ie?await ra(re):re??ln(ue,N);return[ie?le:void 0,await Ea(le)]}}}),Lt,li,Fr,Vi,za,Ca,Aa,Wi,st,kr=m(()=>{qi(),li=!1,Fr=!1,Vi=!1,za=()=>{if(typeof SharedArrayBuffer>"u")return!1;try{return typeof MessageChannel<"u"&&new MessageChannel().port1.postMessage(new SharedArrayBuffer(1)),WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,5,4,1,3,1,1,10,11,1,9,0,65,0,254,16,2,0,26,11]))}catch{return!1}},Ca=()=>{try{return WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,10,30,1,28,0,65,0,253,15,253,12,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,253,186,1,26,11]))}catch{return!1}},Aa=()=>{try{return WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,5,1,96,0,1,123,3,2,1,0,10,19,1,17,0,65,1,253,15,65,2,253,15,65,3,253,15,253,147,2,11]))}catch{return!1}},Wi=async z=>{if(li)return Promise.resolve();if(Fr)throw new Error("multiple calls to 'initializeWebAssembly()' detected.");if(Vi)throw new Error("previous call to 'initializeWebAssembly()' failed.");Fr=!0;let N=z.initTimeout,j=z.numThreads;if(z.simd!==!1){if(z.simd==="relaxed"){if(!Aa())throw new Error("Relaxed WebAssembly SIMD is not supported in the current environment.")}else if(!Ca())throw new Error("WebAssembly SIMD is not supported in the current environment.")}let te=za();j>1&&!te&&(typeof self<"u"&&!self.crossOriginIsolated&&console.warn("env.wasm.numThreads is set to "+j+", but this will not work unless you enable crossOriginIsolated mode. See https://web.dev/cross-origin-isolation-guide/ for more info."),console.warn("WebAssembly multi-threading is not supported in the current environment. Falling back to single-threading."),z.numThreads=j=1);let X=z.wasmPaths,ue=typeof X=="string"?X:void 0,re=X==null?void 0:X.mjs,ie=(re==null?void 0:re.href)??re,le=X==null?void 0:X.wasm,ve=(le==null?void 0:le.href)??le,Se=z.wasmBinary,[be,ce]=await Ia(ie,ue,j>1,!!Se||!!ve),Fe=!1,oe=[];if(N>0&&oe.push(new Promise(ke=>{setTimeout(()=>{Fe=!0,ke()},N)})),oe.push(new Promise((ke,qt)=>{let dt={numThreads:j};if(Se)dt.wasmBinary=Se,dt.locateFile=lt=>lt;else if(ve||ue)dt.locateFile=lt=>ve??ue+lt;else if(ie&&ie.indexOf("blob:")!==0)dt.locateFile=lt=>new URL(lt,ie).href;else if(be){let lt=ta();lt&&(dt.locateFile=Mt=>lt+Mt)}ce(dt).then(lt=>{Fr=!1,li=!0,Lt=lt,ke(),be&&URL.revokeObjectURL(be)},lt=>{Fr=!1,Vi=!0,qt(lt)})})),await Promise.race(oe),Fe)throw new Error(`WebAssembly backend initializing failed due to timeout: ${N}ms`)},st=()=>{if(li&&Lt)return Lt;throw new Error("WebAssembly is not initialized yet.")}}),Ht,di,Ye,Gi=m(()=>{kr(),Ht=(z,N)=>{let j=st(),te=j.lengthBytesUTF8(z)+1,X=j._malloc(te);return j.stringToUTF8(z,X,te),N.push(X),X},di=(z,N,j,te)=>{if(typeof z=="object"&&z!==null){if(j.has(z))throw new Error("Circular reference in options");j.add(z)}Object.entries(z).forEach(([X,ue])=>{let re=N?N+X:X;if(typeof ue=="object")di(ue,re+".",j,te);else if(typeof ue=="string"||typeof ue=="number")te(re,ue.toString());else if(typeof ue=="boolean")te(re,ue?"1":"0");else throw new Error(`Can't handle extra config type: ${typeof ue}`)})},Ye=z=>{let N=st(),j=N.stackSave();try{let te=N.PTR_SIZE,X=N.stackAlloc(2*te);N._OrtGetLastError(X,X+te);let ue=Number(N.getValue(X,te===4?"i32":"i64")),re=N.getValue(X+te,"*"),ie=re?N.UTF8ToString(re):"";throw new Error(`${z} ERROR_CODE: ${ue}, ERROR_MESSAGE: ${ie}`)}finally{N.stackRestore(j)}}}),pn,Hr=m(()=>{kr(),Gi(),pn=z=>{let N=st(),j=0,te=[],X=z||{};try{if((z==null?void 0:z.logSeverityLevel)===void 0)X.logSeverityLevel=2;else if(typeof z.logSeverityLevel!="number"||!Number.isInteger(z.logSeverityLevel)||z.logSeverityLevel<0||z.logSeverityLevel>4)throw new Error(`log severity level is not valid: ${z.logSeverityLevel}`);if((z==null?void 0:z.logVerbosityLevel)===void 0)X.logVerbosityLevel=0;else if(typeof z.logVerbosityLevel!="number"||!Number.isInteger(z.logVerbosityLevel))throw new Error(`log verbosity level is not valid: ${z.logVerbosityLevel}`);(z==null?void 0:z.terminate)===void 0&&(X.terminate=!1);let ue=0;return(z==null?void 0:z.tag)!==void 0&&(ue=Ht(z.tag,te)),j=N._OrtCreateRunOptions(X.logSeverityLevel,X.logVerbosityLevel,!!X.terminate,ue),j===0&&Ye("Can't create run options."),(z==null?void 0:z.extra)!==void 0&&di(z.extra,"",new WeakSet,(re,ie)=>{let le=Ht(re,te),ve=Ht(ie,te);N._OrtAddRunConfigEntry(j,le,ve)!==0&&Ye(`Can't set a run config entry: ${re} - ${ie}.`)}),[j,te]}catch(ue){throw j!==0&&N._OrtReleaseRunOptions(j),te.forEach(re=>N._free(re)),ue}}}),ia,aa,pi,Zt,Oa,cn,_s=m(()=>{kr(),Gi(),ia=z=>{switch(z){case"disabled":return 0;case"basic":return 1;case"extended":return 2;case"layout":return 3;case"all":return 99;default:throw new Error(`unsupported graph optimization level: ${z}`)}},aa=z=>{switch(z){case"sequential":return 0;case"parallel":return 1;default:throw new Error(`unsupported execution mode: ${z}`)}},pi=z=>{z.extra||(z.extra={}),z.extra.session||(z.extra.session={});let N=z.extra.session;N.use_ort_model_bytes_directly||(N.use_ort_model_bytes_directly="1"),z.executionProviders&&z.executionProviders.some(j=>(typeof j=="string"?j:j.name)==="webgpu")&&(z.enableMemPattern=!1)},Zt=(z,N,j,te)=>{let X=Ht(N,te),ue=Ht(j,te);st()._OrtAddSessionConfigEntry(z,X,ue)!==0&&Ye(`Can't set a session config entry: ${N} - ${j}.`)},Oa=async(z,N,j)=>{let te=N.executionProviders;for(let X of te){let ue=typeof X=="string"?X:X.name,re=[];switch(ue){case"webnn":if(ue="WEBNN",Zt(z,"session.disable_quant_qdq","1",j),Zt(z,"session.disable_qdq_constant_folding","1",j),typeof X!="string"){let be=X==null?void 0:X.deviceType;be&&Zt(z,"deviceType",be,j)}break;case"webgpu":if(ue="JS",typeof X!="string"){let be=X;if(be!=null&&be.preferredLayout){if(be.preferredLayout!=="NCHW"&&be.preferredLayout!=="NHWC")throw new Error(`preferredLayout must be either 'NCHW' or 'NHWC': ${be.preferredLayout}`);Zt(z,"preferredLayout",be.preferredLayout,j)}}break;case"wasm":case"cpu":continue;default:throw new Error(`not supported execution provider: ${ue}`)}let ie=Ht(ue,j),le=re.length,ve=0,Se=0;if(le>0){ve=st()._malloc(le*st().PTR_SIZE),j.push(ve),Se=st()._malloc(le*st().PTR_SIZE),j.push(Se);for(let be=0;be<le;be++)st().setValue(ve+be*st().PTR_SIZE,re[be][0],"*"),st().setValue(Se+be*st().PTR_SIZE,re[be][1],"*")}await st()._OrtAppendExecutionProvider(z,ie,ve,Se,le)!==0&&Ye(`Can't append execution provider: ${ue}.`)}},cn=async z=>{let N=st(),j=0,te=[],X=z||{};pi(X);try{let ue=ia(X.graphOptimizationLevel??"all"),re=aa(X.executionMode??"sequential"),ie=typeof X.logId=="string"?Ht(X.logId,te):0,le=X.logSeverityLevel??2;if(!Number.isInteger(le)||le<0||le>4)throw new Error(`log severity level is not valid: ${le}`);let ve=X.logVerbosityLevel??0;if(!Number.isInteger(ve)||ve<0||ve>4)throw new Error(`log verbosity level is not valid: ${ve}`);let Se=typeof X.optimizedModelFilePath=="string"?Ht(X.optimizedModelFilePath,te):0;if(j=N._OrtCreateSessionOptions(ue,!!X.enableCpuMemArena,!!X.enableMemPattern,re,!!X.enableProfiling,0,ie,le,ve,Se),j===0&&Ye("Can't create session options."),X.executionProviders&&await Oa(j,X,te),X.enableGraphCapture!==void 0){if(typeof X.enableGraphCapture!="boolean")throw new Error(`enableGraphCapture must be a boolean value: ${X.enableGraphCapture}`);Zt(j,"enableGraphCapture",X.enableGraphCapture.toString(),te)}if(X.freeDimensionOverrides)for(let[be,ce]of Object.entries(X.freeDimensionOverrides)){if(typeof be!="string")throw new Error(`free dimension override name must be a string: ${be}`);if(typeof ce!="number"||!Number.isInteger(ce)||ce<0)throw new Error(`free dimension override value must be a non-negative integer: ${ce}`);let Fe=Ht(be,te);N._OrtAddFreeDimensionOverride(j,Fe,ce)!==0&&Ye(`Can't set a free dimension override: ${be} - ${ce}.`)}return X.extra!==void 0&&di(X.extra,"",new WeakSet,(be,ce)=>{Zt(j,be,ce,te)}),[j,te]}catch(ue){throw j!==0&&N._OrtReleaseSessionOptions(j)!==0&&Ye("Can't release session options."),te.forEach(re=>N._free(re)),ue}}}),Er,rr,wr,pr,fr,xi,na,Ra,it=m(()=>{Er=z=>{switch(z){case"int8":return 3;case"uint8":return 2;case"bool":return 9;case"int16":return 5;case"uint16":return 4;case"int32":return 6;case"uint32":return 12;case"float16":return 10;case"float32":return 1;case"float64":return 11;case"string":return 8;case"int64":return 7;case"uint64":return 13;case"int4":return 22;case"uint4":return 21;default:throw new Error(`unsupported data type: ${z}`)}},rr=z=>{switch(z){case 3:return"int8";case 2:return"uint8";case 9:return"bool";case 5:return"int16";case 4:return"uint16";case 6:return"int32";case 12:return"uint32";case 10:return"float16";case 1:return"float32";case 11:return"float64";case 8:return"string";case 7:return"int64";case 13:return"uint64";case 22:return"int4";case 21:return"uint4";default:throw new Error(`unsupported data type: ${z}`)}},wr=(z,N)=>{let j=[-1,4,1,1,2,2,4,8,-1,1,2,8,4,8,-1,-1,-1,-1,-1,-1,-1,.5,.5][z],te=typeof N=="number"?N:N.reduce((X,ue)=>X*ue,1);return j>0?Math.ceil(te*j):void 0},pr=z=>{switch(z){case"float16":return typeof Float16Array<"u"&&Float16Array.from?Float16Array:Uint16Array;case"float32":return Float32Array;case"uint8":return Uint8Array;case"int8":return Int8Array;case"uint16":return Uint16Array;case"int16":return Int16Array;case"int32":return Int32Array;case"bool":return Uint8Array;case"float64":return Float64Array;case"uint32":return Uint32Array;case"int64":return BigInt64Array;case"uint64":return BigUint64Array;default:throw new Error(`unsupported type: ${z}`)}},fr=z=>{switch(z){case"verbose":return 0;case"info":return 1;case"warning":return 2;case"error":return 3;case"fatal":return 4;default:throw new Error(`unsupported logging level: ${z}`)}},xi=z=>z==="float32"||z==="float16"||z==="int32"||z==="int64"||z==="uint32"||z==="uint8"||z==="bool"||z==="uint4"||z==="int4",na=z=>z==="float32"||z==="float16"||z==="int32"||z==="int64"||z==="uint32"||z==="uint64"||z==="int8"||z==="uint8"||z==="bool"||z==="uint4"||z==="int4",Ra=z=>{switch(z){case"none":return 0;case"cpu":return 1;case"cpu-pinned":return 2;case"texture":return 3;case"gpu-buffer":return 4;case"ml-tensor":return 5;default:throw new Error(`unsupported data location: ${z}`)}}}),sa,hn=m(()=>{ui(),sa=async z=>{if(typeof z=="string"){let N=await fetch(z);if(!N.ok)throw new Error(`failed to load external data file: ${z}`);let j=N.headers.get("Content-Length"),te=j?parseInt(j,10):0;if(te<1073741824)return new Uint8Array(await N.arrayBuffer());{if(!N.body)throw new Error(`failed to load external data file: ${z}, no response body.`);let X=N.body.getReader(),ue;try{ue=new ArrayBuffer(te)}catch(ie){if(ie instanceof RangeError){let le=Math.ceil(te/65536);ue=new WebAssembly.Memory({initial:le,maximum:le}).buffer}else throw ie}let re=0;for(;;){let{done:ie,value:le}=await X.read();if(ie)break;let ve=le.byteLength;new Uint8Array(ue,re,ve).set(le),re+=ve}return new Uint8Array(ue,0,te)}}else return z instanceof Blob?new Uint8Array(await z.arrayBuffer()):z instanceof Uint8Array?z:new Uint8Array(z)}}),fn,Br,Si,ci,oa,Ba,mt,Mr,Ma,hi,he,Fi,Da,mn=m(()=>{Kt(),Hr(),_s(),it(),kr(),Gi(),hn(),fn=(z,N)=>{st()._OrtInit(z,N)!==0&&Ye("Can't initialize onnxruntime.")},Br=async z=>{fn(z.wasm.numThreads,fr(z.logLevel))},Si=async(z,N)=>{var te,X;(X=(te=st()).asyncInit)==null||X.call(te);let j=z.webgpu.adapter;if(N==="webgpu"){if(typeof navigator>"u"||!navigator.gpu)throw new Error("WebGPU is not supported in current environment");if(j){if(typeof j.limits!="object"||typeof j.features!="object"||typeof j.requestDevice!="function")throw new Error("Invalid GPU adapter set in `env.webgpu.adapter`. It must be a GPUAdapter object.")}else{let ue=z.webgpu.powerPreference;if(ue!==void 0&&ue!=="low-power"&&ue!=="high-performance")throw new Error(`Invalid powerPreference setting: "${ue}"`);let re=z.webgpu.forceFallbackAdapter;if(re!==void 0&&typeof re!="boolean")throw new Error(`Invalid forceFallbackAdapter setting: "${re}"`);if(j=await navigator.gpu.requestAdapter({powerPreference:ue,forceFallbackAdapter:re}),!j)throw new Error('Failed to get GPU adapter. You may need to enable flag "--enable-unsafe-webgpu" if you are using Chrome.')}}if(N==="webnn"&&(typeof navigator>"u"||!navigator.ml))throw new Error("WebNN is not supported in current environment")},ci=new Map,oa=z=>{let N=st(),j=N.stackSave();try{let te=N.PTR_SIZE,X=N.stackAlloc(2*te);N._OrtGetInputOutputCount(z,X,X+te)!==0&&Ye("Can't get session input/output count.");let ue=te===4?"i32":"i64";return[Number(N.getValue(X,ue)),Number(N.getValue(X+te,ue))]}finally{N.stackRestore(j)}},Ba=(z,N)=>{let j=st(),te=j.stackSave(),X=0;try{let ue=j.PTR_SIZE,re=j.stackAlloc(2*ue);j._OrtGetInputOutputMetadata(z,N,re,re+ue)!==0&&Ye("Can't get session input/output metadata.");let ie=Number(j.getValue(re,"*"));X=Number(j.getValue(re+ue,"*"));let le=j.HEAP32[X/4];if(le===0)return[ie,0];let ve=j.HEAPU32[X/4+1],Se=[];for(let be=0;be<ve;be++){let ce=Number(j.getValue(X+8+be*ue,"*"));Se.push(ce!==0?j.UTF8ToString(ce):Number(j.getValue(X+8+(be+ve)*ue,"*")))}return[ie,le,Se]}finally{j.stackRestore(te),X!==0&&j._OrtFree(X)}},mt=z=>{let N=st(),j=N._malloc(z.byteLength);if(j===0)throw new Error(`Can't create a session. failed to allocate a buffer of size ${z.byteLength}.`);return N.HEAPU8.set(z,j),[j,z.byteLength]},Mr=async(z,N)=>{var Se,be,ce;let j,te,X=st();Array.isArray(z)?[j,te]=z:z.buffer===X.HEAPU8.buffer?[j,te]=[z.byteOffset,z.byteLength]:[j,te]=mt(z);let ue=0,re=0,ie=[],le=[],ve=[];try{if([re,ie]=await cn(N),(N==null?void 0:N.externalData)&&X.mountExternalData){let Dt=[];for(let vt of N.externalData){let ir=typeof vt=="string"?vt:vt.path;Dt.push(sa(typeof vt=="string"?vt:vt.data).then(mr=>{X.mountExternalData(ir,mr)}))}await Promise.all(Dt)}for(let Dt of(N==null?void 0:N.executionProviders)??[])if((typeof Dt=="string"?Dt:Dt.name)==="webnn"){if(X.shouldTransferToMLTensor=!1,typeof Dt!="string"){let vt=Dt,ir=vt==null?void 0:vt.context,mr=vt==null?void 0:vt.gpuDevice,br=vt==null?void 0:vt.deviceType,fa=vt==null?void 0:vt.powerPreference;ir?X.currentContext=ir:mr?X.currentContext=await X.webnnCreateMLContext(mr):X.currentContext=await X.webnnCreateMLContext({deviceType:br,powerPreference:fa})}else X.currentContext=await X.webnnCreateMLContext();break}ue=await X._OrtCreateSession(j,te,re),(Se=X.webgpuOnCreateSession)==null||Se.call(X,ue),ue===0&&Ye("Can't create a session."),(be=X.jsepOnCreateSession)==null||be.call(X),X.currentContext&&(X.webnnRegisterMLContext(ue,X.currentContext),X.currentContext=void 0,X.shouldTransferToMLTensor=!0);let[Fe,oe]=oa(ue),ke=!!(N!=null&&N.enableGraphCapture),qt=[],dt=[],lt=[],Mt=[],Ke=[];for(let Dt=0;Dt<Fe;Dt++){let[vt,ir,mr]=Ba(ue,Dt);vt===0&&Ye("Can't get an input name."),le.push(vt);let br=X.UTF8ToString(vt);qt.push(br),lt.push(ir===0?{name:br,isTensor:!1}:{name:br,isTensor:!0,type:rr(ir),shape:mr})}for(let Dt=0;Dt<oe;Dt++){let[vt,ir,mr]=Ba(ue,Dt+Fe);vt===0&&Ye("Can't get an output name."),ve.push(vt);let br=X.UTF8ToString(vt);dt.push(br),Mt.push(ir===0?{name:br,isTensor:!1}:{name:br,isTensor:!0,type:rr(ir),shape:mr})}return ci.set(ue,[ue,le,ve,null,ke,!1]),[ue,qt,dt,lt,Mt]}catch(Fe){throw le.forEach(oe=>X._OrtFree(oe)),ve.forEach(oe=>X._OrtFree(oe)),ue!==0&&X._OrtReleaseSession(ue)!==0&&Ye("Can't release session."),Fe}finally{X._free(j),re!==0&&X._OrtReleaseSessionOptions(re)!==0&&Ye("Can't release session options."),ie.forEach(Fe=>X._free(Fe)),(ce=X.unmountExternalData)==null||ce.call(X)}},Ma=z=>{var le,ve,Se;let N=st(),j=ci.get(z);if(!j)throw new Error(`cannot release session. invalid session id: ${z}`);let[te,X,ue,re,ie]=j;re&&(ie&&N._OrtClearBoundOutputs(re.handle)!==0&&Ye("Can't clear bound outputs."),N._OrtReleaseBinding(re.handle)!==0&&Ye("Can't release IO binding.")),(le=N.jsepOnReleaseSession)==null||le.call(N,z),(ve=N.webnnOnReleaseSession)==null||ve.call(N,z),(Se=N.webgpuOnReleaseSession)==null||Se.call(N,z),X.forEach(be=>N._OrtFree(be)),ue.forEach(be=>N._OrtFree(be)),N._OrtReleaseSession(te)!==0&&Ye("Can't release session."),ci.delete(z)},hi=async(z,N,j,te,X,ue,re=!1)=>{if(!z){N.push(0);return}let ie=st(),le=ie.PTR_SIZE,ve=z[0],Se=z[1],be=z[3],ce=be,Fe,oe;if(ve==="string"&&(be==="gpu-buffer"||be==="ml-tensor"))throw new Error("String tensor is not supported on GPU.");if(re&&be!=="gpu-buffer")throw new Error(`External buffer must be provided for input/output index ${ue} when enableGraphCapture is true.`);if(be==="gpu-buffer"){let dt=z[2].gpuBuffer;oe=wr(Er(ve),Se);{let lt=ie.jsepRegisterBuffer;if(!lt)throw new Error('Tensor location "gpu-buffer" is not supported without using WebGPU.');Fe=lt(te,ue,dt,oe)}}else if(be==="ml-tensor"){let dt=z[2].mlTensor;oe=wr(Er(ve),Se);let lt=ie.webnnRegisterMLTensor;if(!lt)throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');Fe=lt(te,dt,Er(ve),Se)}else{let dt=z[2];if(Array.isArray(dt)){oe=le*dt.length,Fe=ie._malloc(oe),j.push(Fe);for(let lt=0;lt<dt.length;lt++){if(typeof dt[lt]!="string")throw new TypeError(`tensor data at index ${lt} is not a string`);ie.setValue(Fe+lt*le,Ht(dt[lt],j),"*")}}else{let lt=ie.webnnIsGraphInput,Mt=ie.webnnIsGraphOutput;if(ve!=="string"&&lt&&Mt){let Ke=ie.UTF8ToString(X);if(lt(te,Ke)||Mt(te,Ke)){let Dt=Er(ve);oe=wr(Dt,Se),ce="ml-tensor";let vt=ie.webnnCreateTemporaryTensor,ir=ie.webnnUploadTensor;if(!vt||!ir)throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');let mr=await vt(te,Dt,Se);ir(mr,new Uint8Array(dt.buffer,dt.byteOffset,dt.byteLength)),Fe=mr}else oe=dt.byteLength,Fe=ie._malloc(oe),j.push(Fe),ie.HEAPU8.set(new Uint8Array(dt.buffer,dt.byteOffset,oe),Fe)}else oe=dt.byteLength,Fe=ie._malloc(oe),j.push(Fe),ie.HEAPU8.set(new Uint8Array(dt.buffer,dt.byteOffset,oe),Fe)}}let ke=ie.stackSave(),qt=ie.stackAlloc(4*Se.length);try{Se.forEach((lt,Mt)=>ie.setValue(qt+Mt*le,lt,le===4?"i32":"i64"));let dt=ie._OrtCreateTensor(Er(ve),Fe,oe,qt,Se.length,Ra(ce));dt===0&&Ye(`Can't create tensor for input/output. session=${te}, index=${ue}.`),N.push(dt)}finally{ie.stackRestore(ke)}},he=async(z,N,j,te,X,ue)=>{var cr,Ga,wn;let re=st(),ie=re.PTR_SIZE,le=ci.get(z);if(!le)throw new Error(`cannot run inference. invalid session id: ${z}`);let ve=le[0],Se=le[1],be=le[2],ce=le[3],Fe=le[4];le[5];let oe=N.length,ke=te.length,qt=0,dt=[],lt=[],Mt=[],Ke=[],Dt=[],vt=re.stackSave(),ir=re.stackAlloc(oe*ie),mr=re.stackAlloc(oe*ie),br=re.stackAlloc(ke*ie),fa=re.stackAlloc(ke*ie);try{[qt,dt]=pn(ue),sr("wasm prepareInputOutputTensor");for(let wt=0;wt<oe;wt++)await hi(j[wt],lt,Ke,z,Se[N[wt]],N[wt],Fe);for(let wt=0;wt<ke;wt++)await hi(X[wt],Mt,Ke,z,be[te[wt]],oe+te[wt],Fe);tr("wasm prepareInputOutputTensor");for(let wt=0;wt<oe;wt++)re.setValue(ir+wt*ie,lt[wt],"*"),re.setValue(mr+wt*ie,Se[N[wt]],"*");for(let wt=0;wt<ke;wt++)re.setValue(br+wt*ie,Mt[wt],"*"),re.setValue(fa+wt*ie,be[te[wt]],"*");(cr=re.jsepOnRunStart)==null||cr.call(re,ve),(Ga=re.webnnOnRunStart)==null||Ga.call(re,ve);let or;or=await re._OrtRun(ve,mr,ir,oe,fa,ke,br,qt),or!==0&&Ye("failed to call OrtRun().");let Kr=[],bn=[];sr("wasm ProcessOutputTensor");for(let wt=0;wt<ke;wt++){let Nr=Number(re.getValue(br+wt*ie,"*"));if(Nr===Mt[wt]||Dt.includes(Mt[wt])){Kr.push(X[wt]),Nr!==Mt[wt]&&re._OrtReleaseTensor(Nr)!==0&&Ye("Can't release tensor.");continue}let Jn=re.stackSave(),gr=re.stackAlloc(4*ie),yi=!1,Qt,$r=0;try{re._OrtGetTensorData(Nr,gr,gr+ie,gr+2*ie,gr+3*ie)!==0&&Ye(`Can't access output tensor data on index ${wt}.`);let Ei=ie===4?"i32":"i64",vr=Number(re.getValue(gr,Ei));$r=re.getValue(gr+ie,"*");let $n=re.getValue(gr+ie*2,"*"),Fa=Number(re.getValue(gr+ie*3,Ei)),Pr=[];for(let Xt=0;Xt<Fa;Xt++)Pr.push(Number(re.getValue($n+Xt*ie,Ei)));re._OrtFree($n)!==0&&Ye("Can't free memory for tensor dims.");let Zr=Pr.reduce((Xt,Gt)=>Xt*Gt,1);Qt=rr(vr);let Ii=ce==null?void 0:ce.outputPreferredLocations[te[wt]];if(Qt==="string"){if(Ii==="gpu-buffer"||Ii==="ml-tensor")throw new Error("String tensor is not supported on GPU.");let Xt=[];for(let Gt=0;Gt<Zr;Gt++){let Ur=re.getValue($r+Gt*ie,"*"),vn=re.getValue($r+(Gt+1)*ie,"*"),es=Gt===Zr-1?void 0:vn-Ur;Xt.push(re.UTF8ToString(Ur,es))}Kr.push([Qt,Pr,Xt,"cpu"])}else if(Ii==="gpu-buffer"&&Zr>0){let Xt=re.jsepGetBuffer;if(!Xt)throw new Error('preferredLocation "gpu-buffer" is not supported without using WebGPU.');let Gt=Xt($r),Ur=wr(vr,Zr);if(Ur===void 0||!xi(Qt))throw new Error(`Unsupported data type: ${Qt}`);yi=!0,Kr.push([Qt,Pr,{gpuBuffer:Gt,download:re.jsepCreateDownloader(Gt,Ur,Qt),dispose:()=>{re._OrtReleaseTensor(Nr)!==0&&Ye("Can't release tensor.")}},"gpu-buffer"])}else if(Ii==="ml-tensor"&&Zr>0){let Xt=re.webnnEnsureTensor,Gt=re.webnnIsGraphInputOutputTypeSupported;if(!Xt||!Gt)throw new Error('preferredLocation "ml-tensor" is not supported without using WebNN.');if(wr(vr,Zr)===void 0||!na(Qt))throw new Error(`Unsupported data type: ${Qt}`);if(!Gt(z,Qt,!1))throw new Error(`preferredLocation "ml-tensor" for ${Qt} output is not supported by current WebNN Context.`);let Ur=await Xt(z,$r,vr,Pr,!1);yi=!0,Kr.push([Qt,Pr,{mlTensor:Ur,download:re.webnnCreateMLTensorDownloader($r,Qt),dispose:()=>{re.webnnReleaseTensorId($r),re._OrtReleaseTensor(Nr)}},"ml-tensor"])}else if(Ii==="ml-tensor-cpu-output"&&Zr>0){let Xt=re.webnnCreateMLTensorDownloader($r,Qt)(),Gt=Kr.length;yi=!0,bn.push((async()=>{let Ur=[Gt,await Xt];return re.webnnReleaseTensorId($r),re._OrtReleaseTensor(Nr),Ur})()),Kr.push([Qt,Pr,[],"cpu"])}else{let Xt=pr(Qt),Gt=new Xt(Zr);new Uint8Array(Gt.buffer,Gt.byteOffset,Gt.byteLength).set(re.HEAPU8.subarray($r,$r+Gt.byteLength)),Kr.push([Qt,Pr,Gt,"cpu"])}}finally{re.stackRestore(Jn),Qt==="string"&&$r&&re._free($r),yi||re._OrtReleaseTensor(Nr)}}ce&&!Fe&&(re._OrtClearBoundOutputs(ce.handle)!==0&&Ye("Can't clear bound outputs."),ci.set(z,[ve,Se,be,ce,Fe,!1]));for(let[wt,Nr]of await Promise.all(bn))Kr[wt][2]=Nr;return tr("wasm ProcessOutputTensor"),Kr}finally{(wn=re.webnnOnRunEnd)==null||wn.call(re,ve),re.stackRestore(vt),lt.forEach(or=>re._OrtReleaseTensor(or)),Mt.forEach(or=>re._OrtReleaseTensor(or)),Ke.forEach(or=>re._free(or)),qt!==0&&re._OrtReleaseRunOptions(qt),dt.forEach(or=>re._free(or))}},Fi=z=>{let N=st(),j=ci.get(z);if(!j)throw new Error("invalid session id");let te=j[0],X=N._OrtEndProfiling(te);X===0&&Ye("Can't get an profile file name."),N._OrtFree(X)},Da=z=>{let N=[];for(let j of z){let te=j[2];!Array.isArray(te)&&"buffer"in te&&N.push(te.buffer)}return N}}),zr,Xe,jr,Jr,Ti,fi,ua,la,ei,mi,ki,Na,Dr,Cr,Pa,da,gi,Ua,La=m(()=>{Kt(),mn(),kr(),qi(),zr=()=>!!B.wasm.proxy&&typeof document<"u",jr=!1,Jr=!1,Ti=!1,la=new Map,ei=(z,N)=>{let j=la.get(z);j?j.push(N):la.set(z,[N])},mi=()=>{if(jr||!Jr||Ti||!Xe)throw new Error("worker not ready")},ki=z=>{switch(z.data.type){case"init-wasm":jr=!1,z.data.err?(Ti=!0,ua[1](z.data.err)):(Jr=!0,ua[0]()),fi&&(URL.revokeObjectURL(fi),fi=void 0);break;case"init-ep":case"copy-from":case"create":case"release":case"run":case"end-profiling":{let N=la.get(z.data.type);z.data.err?N.shift()[1](z.data.err):N.shift()[0](z.data.out);break}}},Na=async()=>{if(!Jr){if(jr)throw new Error("multiple calls to 'initWasm()' detected.");if(Ti)throw new Error("previous call to 'initWasm()' failed.");if(jr=!0,zr())return new Promise((z,N)=>{Xe==null||Xe.terminate(),dn().then(([j,te])=>{try{Xe=te,Xe.onerror=ue=>N(ue),Xe.onmessage=ki,ua=[z,N];let X={type:"init-wasm",in:B};if(!X.in.wasm.wasmPaths&&j){let ue=ta();ue&&(X.in.wasm.wasmPaths=ue)}Xe.postMessage(X),fi=j}catch(X){N(X)}},N)});try{await Wi(B.wasm),await Br(B),Jr=!0}catch(z){throw Ti=!0,z}finally{jr=!1}}},Dr=async z=>{if(zr())return mi(),new Promise((N,j)=>{ei("init-ep",[N,j]);let te={type:"init-ep",in:{epName:z,env:B}};Xe.postMessage(te)});await Si(B,z)},Cr=async z=>zr()?(mi(),new Promise((N,j)=>{ei("copy-from",[N,j]);let te={type:"copy-from",in:{buffer:z}};Xe.postMessage(te,[z.buffer])})):mt(z),Pa=async(z,N)=>{if(zr()){if(N!=null&&N.preferredOutputLocation)throw new Error('session option "preferredOutputLocation" is not supported for proxy.');return mi(),new Promise((j,te)=>{ei("create",[j,te]);let X={type:"create",in:{model:z,options:{...N}}},ue=[];z instanceof Uint8Array&&ue.push(z.buffer),Xe.postMessage(X,ue)})}else return Mr(z,N)},da=async z=>{if(zr())return mi(),new Promise((N,j)=>{ei("release",[N,j]);let te={type:"release",in:z};Xe.postMessage(te)});Ma(z)},gi=async(z,N,j,te,X,ue)=>{if(zr()){if(j.some(re=>re[3]!=="cpu"))throw new Error("input tensor on GPU is not supported for proxy.");if(X.some(re=>re))throw new Error("pre-allocated output tensor is not supported for proxy.");return mi(),new Promise((re,ie)=>{ei("run",[re,ie]);let le=j,ve={type:"run",in:{sessionId:z,inputIndices:N,inputs:le,outputIndices:te,options:ue}};Xe.postMessage(ve,Da(le))})}else return he(z,N,j,te,X,ue)},Ua=async z=>{if(zr())return mi(),new Promise((N,j)=>{ei("end-profiling",[N,j]);let te={type:"end-profiling",in:z};Xe.postMessage(te)});Fi(z)}}),gn,Hi,qa,pa=m(()=>{Kt(),La(),it(),ui(),hn(),gn=(z,N)=>{switch(z.location){case"cpu":return[z.type,z.dims,z.data,"cpu"];case"gpu-buffer":return[z.type,z.dims,{gpuBuffer:z.gpuBuffer},"gpu-buffer"];case"ml-tensor":return[z.type,z.dims,{mlTensor:z.mlTensor},"ml-tensor"];default:throw new Error(`invalid data location: ${z.location} for ${N()}`)}},Hi=z=>{switch(z[3]){case"cpu":return new St(z[0],z[2],z[1]);case"gpu-buffer":{let N=z[0];if(!xi(N))throw new Error(`not supported data type: ${N} for deserializing GPU tensor`);let{gpuBuffer:j,download:te,dispose:X}=z[2];return St.fromGpuBuffer(j,{dataType:N,dims:z[1],download:te,dispose:X})}case"ml-tensor":{let N=z[0];if(!na(N))throw new Error(`not supported data type: ${N} for deserializing MLTensor tensor`);let{mlTensor:j,download:te,dispose:X}=z[2];return St.fromMLTensor(j,{dataType:N,dims:z[1],download:te,dispose:X})}default:throw new Error(`invalid data location: ${z[3]}`)}},qa=class{async fetchModelAndCopyToWasmMemory(z){return Cr(await sa(z))}async loadModel(z,N){He();let j;typeof z=="string"?j=await this.fetchModelAndCopyToWasmMemory(z):j=z,[this.sessionId,this.inputNames,this.outputNames,this.inputMetadata,this.outputMetadata]=await Pa(j,N),Rt()}async dispose(){return da(this.sessionId)}async run(z,N,j){He();let te=[],X=[];Object.entries(z).forEach(be=>{let ce=be[0],Fe=be[1],oe=this.inputNames.indexOf(ce);if(oe===-1)throw new Error(`invalid input '${ce}'`);te.push(Fe),X.push(oe)});let ue=[],re=[];Object.entries(N).forEach(be=>{let ce=be[0],Fe=be[1],oe=this.outputNames.indexOf(ce);if(oe===-1)throw new Error(`invalid output '${ce}'`);ue.push(Fe),re.push(oe)});let ie=te.map((be,ce)=>gn(be,()=>`input "${this.inputNames[X[ce]]}"`)),le=ue.map((be,ce)=>be?gn(be,()=>`output "${this.outputNames[re[ce]]}"`):null),ve=await gi(this.sessionId,X,ie,re,le,j),Se={};for(let be=0;be<ve.length;be++)Se[this.outputNames[re[be]]]=ue[be]??Hi(ve[be]);return Rt(),Se}startProfiling(){}endProfiling(){Ua(this.sessionId)}}}),ca={};_(ca,{OnnxruntimeWebAssemblyBackend:()=>Va,initializeFlags:()=>ji,wasmBackend:()=>Wa});var ji,Va,Wa,yn=m(()=>{Kt(),La(),pa(),ji=()=>{(typeof B.wasm.initTimeout!="number"||B.wasm.initTimeout<0)&&(B.wasm.initTimeout=0);let z=B.wasm.simd;if(typeof z!="boolean"&&z!==void 0&&z!=="fixed"&&z!=="relaxed"&&(console.warn(`Property "env.wasm.simd" is set to unknown value "${z}". Reset it to \`false\` and ignore SIMD feature checking.`),B.wasm.simd=!1),typeof B.wasm.proxy!="boolean"&&(B.wasm.proxy=!1),typeof B.wasm.trace!="boolean"&&(B.wasm.trace=!1),typeof B.wasm.numThreads!="number"||!Number.isInteger(B.wasm.numThreads)||B.wasm.numThreads<=0)if(typeof self<"u"&&!self.crossOriginIsolated)B.wasm.numThreads=1;else{let N=typeof navigator>"u"?g("node:os").cpus().length:navigator.hardwareConcurrency;B.wasm.numThreads=Math.min(4,Math.ceil((N||1)/2))}},Va=class{async init(z){ji(),await Na(),await Dr(z)}async createInferenceSessionHandler(z,N){let j=new qa;return await j.loadModel(z,N),j}},Wa=new Va}),_n={};_(_n,{InferenceSession:()=>Rr,TRACE:()=>hr,TRACE_EVENT_BEGIN:()=>sr,TRACE_EVENT_END:()=>tr,TRACE_FUNC_BEGIN:()=>He,TRACE_FUNC_END:()=>Rt,Tensor:()=>St,default:()=>ws,env:()=>B,registerBackend:()=>A}),Kt(),Kt(),Kt();var ha="1.26.0",ws=sn;{let z=(yn(),x(ca)).wasmBackend;A("cpu",z,10),A("wasm",z,10)}return Object.defineProperty(B.versions,"web",{value:ha,enumerable:!0}),x(_n)})();e.exports=a})(od)),od.exports}var tg;function k2(){return tg||(tg=1,(function(e){var r=va&&va.__createBinding||(Object.create?(function(F,G,K,ee){ee===void 0&&(ee=K);var ae=Object.getOwnPropertyDescriptor(G,K);(!ae||("get"in ae?!G.__esModule:ae.writable||ae.configurable))&&(ae={enumerable:!0,get:function(){return G[K]}}),Object.defineProperty(F,ee,ae)}):(function(F,G,K,ee){ee===void 0&&(ee=K),F[ee]=G[K]})),a=va&&va.__setModuleDefault||(Object.create?(function(F,G){Object.defineProperty(F,"default",{enumerable:!0,value:G})}):function(F,G){F.default=G}),s=va&&va.__importStar||function(F){if(F&&F.__esModule)return F;var G={};if(F!=null)for(var K in F)K!=="default"&&Object.prototype.hasOwnProperty.call(F,K)&&r(G,F,K);return a(G,F),G};Object.defineProperty(e,"__esModule",{value:!0}),e.MicVAD=e.getDefaultRealTimeVADOptions=e.ort=e.DEFAULT_MODEL=void 0;const o=s(T2()),p=bp(),d=$p(),g=Ho(),m=ll(),_=Uw(),v=Lw();e.DEFAULT_MODEL="legacy",e.ort=o;const x="vad.worklet.bundle.min.js",T="silero_vad_v5.onnx",C="silero_vad_legacy.onnx",A=F=>({...d.defaultFrameProcessorOptions,onFrameProcessed:()=>{},onVADMisfire:()=>{g.log.debug("VAD misfire")},onSpeechStart:()=>{g.log.debug("Detected speech start")},onSpeechEnd:()=>{g.log.debug("Detected speech end")},onSpeechRealStart:()=>{g.log.debug("Detected real speech start")},baseAssetPath:"./",onnxWASMBasePath:"./",model:F,workletOptions:{},getStream:async()=>await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:!0,autoGainControl:!0,noiseSuppression:!0}}),pauseStream:async G=>{G.getTracks().forEach(K=>{K.stop()})},resumeStream:async()=>await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:!0,autoGainControl:!0,noiseSuppression:!0}}),ortConfig:G=>{G.env.logLevel="error"},startOnLoad:!0,processorType:"auto"});e.getDefaultRealTimeVADOptions=A;const R=F=>"audioWorklet"in F&&typeof AudioWorkletNode=="function"?"AudioWorklet":"ScriptProcessor";async function H(F,G,K,ee,ae){await K.audioWorklet.addModule(F),G.processorOptions={...G.processorOptions??{},frameSamples:ee};const B=new AudioWorkletNode(K,"vad-helper-worklet",G);return B.port.onmessage=async me=>{const _e=me.data;if(!(typeof _e=="object"&&_e&&"message"in _e)){console.error("Invalid message event",_e);return}switch(_e.message){case m.Message.AudioFrame:{if(!("data"in _e&&_e.data instanceof ArrayBuffer)){console.log("Audio frame message has no data");return}const Re=new Float32Array(_e.data);await ae(Re);break}}},B}async function U(F,G,K){const ee=new v.Resampler({nativeSampleRate:F.sampleRate,targetSampleRate:16e3,targetFrameSize:G});g.log.debug("using script processor");const B=F.createScriptProcessor(4096,1,1);let me=!1;return B.onaudioprocess=async _e=>{if(!me){me=!0;try{const Re=_e.inputBuffer.getChannelData(0);_e.outputBuffer.getChannelData(0).fill(0);const Me=ee.process(Re);for(const pe of Me)await K(pe)}catch(Re){console.error("Error processing audio:",Re)}finally{me=!1}}},B.connect(F.destination),B}class P{constructor(G,K,ee,ae,B=!1,me=null,_e=null,Re=null,Ue=null,Me=null,pe=null,qe="uninitialized",Ve=!1){this.options=G,this.frameProcessor=K,this.model=ee,this.frameSamples=ae,this.listening=B,this.errored=me,this._stream=_e,this._audioContext=Re,this._vadNode=Ue,this._mediaStreamAudioSourceNode=Me,this._audioProcessorAdapterType=pe,this.initializationState=qe,this.ownsAudioContext=Ve,this.getAudioInstances=()=>{if(this._stream===null||this._audioContext===null||this._vadNode==null||this._mediaStreamAudioSourceNode==null)throw new Error("MicVAD has null stream, audio context, or processor adapter");return{stream:this._stream,audioContext:this._audioContext,vadNode:this._vadNode,mediaStreamAudioSourceNode:this._mediaStreamAudioSourceNode}},this.setErrored=ze=>{this.initializationState="errored",this.errored=ze},this.start=async()=>{switch(this.initializationState){case"uninitialized":{g.log.debug("initializing micVAD"),this.initializationState="initializing",this.frameProcessor.resume();try{this._stream=await this.options.getStream()}catch(ze){throw ze instanceof Error?this.setErrored(ze.message):this.setErrored(String(ze)),ze}if(this.options.audioContext?(console.log("using custom audio context"),this._audioContext=this.options.audioContext):(console.log("using default audio context"),this._audioContext=new AudioContext,this.ownsAudioContext=!0),!this._audioContext)throw this.setErrored("Audio context is null"),Error("Audio context is null");switch(this._audioProcessorAdapterType=this.options.processorType=="auto"?R(this._audioContext):this.options.processorType,this._audioProcessorAdapterType){case"AudioWorklet":this._vadNode=await H(this.options.baseAssetPath+x,this.options.workletOptions,this._audioContext,this.frameSamples,this.processFrame);break;case"ScriptProcessor":this._vadNode=await U(this._audioContext,this.frameSamples,this.processFrame);break;default:throw new Error(`Unsupported audio processor adapter type: ${this._audioProcessorAdapterType}`)}this._mediaStreamAudioSourceNode=new MediaStreamAudioSourceNode(this._audioContext,{mediaStream:this._stream}),this._mediaStreamAudioSourceNode.connect(this._vadNode),g.log.debug("started micVAD"),this.listening=!0,this.initializationState="initialized";break}case"initializing":{g.log.warn("start called while initializing");break}case"initialized":{if(this.listening)return;this.listening=!0,this.frameProcessor.resume();const{stream:ze,audioContext:ht,vadNode:Ce}=this.getAudioInstances();this._stream=await this.options.resumeStream(ze);const nt=new MediaStreamAudioSourceNode(ht,{mediaStream:this._stream});this._mediaStreamAudioSourceNode=nt,nt.connect(Ce);break}case"destroyed":{g.log.warn("start called after destroyed");break}case"errored":{g.log.error("start called after errored");break}default:{g.log.warn("weird initialization state");break}}},this.pause=async()=>{if(!this.listening)return;this.listening=!1;const{stream:ze,mediaStreamAudioSourceNode:ht}=this.getAudioInstances();await this.options.pauseStream(ze),ht.disconnect(),this.frameProcessor.pause(this.handleFrameProcessorEvent)},this.destroy=async()=>{var ht;g.log.debug("destroy called"),this.initializationState="destroyed";const{vadNode:ze}=this.getAudioInstances();ze instanceof AudioWorkletNode&&ze.port.postMessage(m.Message.SpeechStop),this.listening&&await this.pause(),await this.model.release(),this.ownsAudioContext&&await((ht=this._audioContext)==null?void 0:ht.close())},this.setOptions=ze=>{this.frameProcessor.setOptions(ze)},this.processFrame=async ze=>{await this.frameProcessor.process(ze,this.handleFrameProcessorEvent)},this.handleFrameProcessorEvent=ze=>{switch(ze.msg){case m.Message.FrameProcessed:this.options.onFrameProcessed(ze.probs,ze.frame);break;case m.Message.SpeechStart:this.options.onSpeechStart();break;case m.Message.SpeechRealStart:this.options.onSpeechRealStart();break;case m.Message.VADMisfire:this.options.onVADMisfire();break;case m.Message.SpeechEnd:this.options.onSpeechEnd(ze.audio);break}}}static async new(G={}){const K={...(0,e.getDefaultRealTimeVADOptions)(G.model??e.DEFAULT_MODEL),...G};(0,d.validateOptions)(K),e.ort.env.wasm.wasmPaths=K.onnxWASMBasePath,K.ortConfig!==void 0&&K.ortConfig(e.ort);const ee=K.model==="v5"?T:C,ae=K.baseAssetPath+ee,B=K.model==="v5"?_.SileroV5.new:_.SileroLegacy.new;let me;try{me=await B(e.ort,()=>(0,p.defaultModelFetcher)(ae))}catch(pe){throw console.error(`Encountered an error while loading model file ${ae}`),pe}const _e=K.model==="v5"?512:1536,Re=_e/16,Ue=new d.FrameProcessor(me.process,me.reset_state,{positiveSpeechThreshold:K.positiveSpeechThreshold,negativeSpeechThreshold:K.negativeSpeechThreshold,redemptionMs:K.redemptionMs,preSpeechPadMs:K.preSpeechPadMs,minSpeechMs:K.minSpeechMs,submitUserSpeechOnPause:K.submitUserSpeechOnPause},Re),Me=new P(K,Ue,me,_e);if(K.startOnLoad)try{await Me.start()}catch(pe){throw console.error("Error starting micVad",pe),pe}return Me}}e.MicVAD=P})(va)),va}var rg;function E2(){return rg||(rg=1,(function(e){Object.defineProperty(e,"__esModule",{value:!0}),e.getDefaultRealTimeVADOptions=e.MicVAD=e.DEFAULT_MODEL=e.utils=e.NonRealTimeVAD=e.Message=e.FrameProcessor=e.defaultModelFetcher=e.baseAssetPath=void 0;var r=Dw();Object.defineProperty(e,"baseAssetPath",{enumerable:!0,get:function(){return r.baseAssetPath}});var a=bp();Object.defineProperty(e,"defaultModelFetcher",{enumerable:!0,get:function(){return a.defaultModelFetcher}});var s=$p();Object.defineProperty(e,"FrameProcessor",{enumerable:!0,get:function(){return s.FrameProcessor}});var o=ll();Object.defineProperty(e,"Message",{enumerable:!0,get:function(){return o.Message}});var p=x2();Object.defineProperty(e,"NonRealTimeVAD",{enumerable:!0,get:function(){return p.NonRealTimeVAD}});const d=S2();e.utils={audioFileToArray:d.audioFileToArray,minFramesForTargetMS:d.minFramesForTargetMS,arrayBufferToBase64:d.arrayBufferToBase64,encodeWAV:d.encodeWAV};var g=k2();Object.defineProperty(e,"DEFAULT_MODEL",{enumerable:!0,get:function(){return g.DEFAULT_MODEL}}),Object.defineProperty(e,"MicVAD",{enumerable:!0,get:function(){return g.MicVAD}}),Object.defineProperty(e,"getDefaultRealTimeVADOptions",{enumerable:!0,get:function(){return g.getDefaultRealTimeVADOptions}})})(ad)),ad}var Vw=E2();/*!
 * ONNX Runtime Web v1.26.0
 * Copyright (c) Microsoft Corporation. All rights reserved.
 * Licensed under the MIT License.
 */var vp=Object.defineProperty,I2=Object.getOwnPropertyDescriptor,z2=Object.getOwnPropertyNames,C2=Object.prototype.hasOwnProperty,A2=(e=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(e,{get:(r,a)=>(typeof require<"u"?require:r)[a]}):e)(function(e){if(typeof require<"u")return require.apply(this,arguments);throw Error('Dynamic require of "'+e+'" is not supported')}),Ee=(e,r)=>()=>(e&&(r=e(e=0)),r),ys=(e,r)=>{for(var a in r)vp(e,a,{get:r[a],enumerable:!0})},O2=(e,r,a,s)=>{if(r&&typeof r=="object"||typeof r=="function")for(let o of z2(r))!C2.call(e,o)&&o!==a&&vp(e,o,{get:()=>r[o],enumerable:!(s=I2(r,o))||s.enumerable});return e},Go=e=>O2(vp({},"__esModule",{value:!0}),e),Io,en,hs,ig,Ww,Gw=Ee(()=>{Io=new Map,en=[],hs=(e,r,a)=>{if(r&&typeof r.init=="function"&&typeof r.createInferenceSessionHandler=="function"){let s=Io.get(e);if(s===void 0)Io.set(e,{backend:r,priority:a});else{if(s.priority>a)return;if(s.priority===a&&s.backend!==r)throw new Error(`cannot register backend "${e}" using priority ${a}`)}if(a>=0){let o=en.indexOf(e);o!==-1&&en.splice(o,1);for(let p=0;p<en.length;p++)if(Io.get(en[p]).priority<=a){en.splice(p,0,e);return}en.push(e)}return}throw new TypeError("not a valid backend")},ig=async e=>{let r=Io.get(e);if(!r)return"backend not found.";if(r.initialized)return r.backend;if(r.aborted)return r.error;{let a=!!r.initPromise;try{return a||(r.initPromise=r.backend.init(e)),await r.initPromise,r.initialized=!0,r.backend}catch(s){return a||(r.error=`${s}`,r.aborted=!0),r.error}finally{delete r.initPromise}}},Ww=async e=>{let r=e.executionProviders||[],a=r.map(m=>typeof m=="string"?m:m.name),s=a.length===0?en:a,o,p=[],d=new Set;for(let m of s){let _=await ig(m);typeof _=="string"?p.push({name:m,err:_}):(o||(o=_),o===_&&d.add(m))}if(!o)throw new Error(`no available backend found. ERR: ${p.map(m=>`[${m.name}] ${m.err}`).join(", ")}`);for(let{name:m,err:_}of p)a.includes(m)&&console.warn(`removing requested execution provider "${m}" from session options because it is not available: ${_}`);let g=r.filter(m=>d.has(typeof m=="string"?m:m.name));return[o,new Proxy(e,{get:(m,_)=>_==="executionProviders"?g:Reflect.get(m,_)})]}}),R2=Ee(()=>{Gw()}),Fw,B2=Ee(()=>{Fw="1.26.0"}),ud,Sr,Hw=Ee(()=>{B2(),ud="warning",Sr={wasm:{},webgl:{},webgpu:{},versions:{common:Fw},set logLevel(e){if(e!==void 0){if(typeof e!="string"||["verbose","info","warning","error","fatal"].indexOf(e)===-1)throw new Error(`Unsupported logging level: ${e}`);ud=e}},get logLevel(){return ud}},Object.defineProperty(Sr,"logLevel",{enumerable:!0})}),Ft,M2=Ee(()=>{Hw(),Ft=Sr}),jw,Kw,D2=Ee(()=>{jw=(e,r)=>{let a=typeof document<"u"?document.createElement("canvas"):new OffscreenCanvas(1,1);a.width=e.dims[3],a.height=e.dims[2];let s=a.getContext("2d");if(s!=null){let o,p;(r==null?void 0:r.tensorLayout)!==void 0&&r.tensorLayout==="NHWC"?(o=e.dims[2],p=e.dims[3]):(o=e.dims[3],p=e.dims[2]);let d=(r==null?void 0:r.format)!==void 0?r.format:"RGB",g=r==null?void 0:r.norm,m,_;g===void 0||g.mean===void 0?m=[255,255,255,255]:typeof g.mean=="number"?m=[g.mean,g.mean,g.mean,g.mean]:(m=[g.mean[0],g.mean[1],g.mean[2],0],g.mean[3]!==void 0&&(m[3]=g.mean[3])),g===void 0||g.bias===void 0?_=[0,0,0,0]:typeof g.bias=="number"?_=[g.bias,g.bias,g.bias,g.bias]:(_=[g.bias[0],g.bias[1],g.bias[2],0],g.bias[3]!==void 0&&(_[3]=g.bias[3]));let v=p*o,x=0,T=v,C=v*2,A=-1;d==="RGBA"?(x=0,T=v,C=v*2,A=v*3):d==="RGB"?(x=0,T=v,C=v*2):d==="RBG"&&(x=0,C=v,T=v*2);for(let R=0;R<p;R++)for(let H=0;H<o;H++){let U=(e.data[x++]-_[0])*m[0],P=(e.data[T++]-_[1])*m[1],F=(e.data[C++]-_[2])*m[2],G=A===-1?255:(e.data[A++]-_[3])*m[3];s.fillStyle="rgba("+U+","+P+","+F+","+G+")",s.fillRect(H,R,1,1)}if("toDataURL"in a)return a.toDataURL();throw new Error("toDataURL is not supported")}else throw new Error("Can not access image data")},Kw=(e,r)=>{let a=typeof document<"u"?document.createElement("canvas").getContext("2d"):new OffscreenCanvas(1,1).getContext("2d"),s;if(a!=null){let o,p,d;(r==null?void 0:r.tensorLayout)!==void 0&&r.tensorLayout==="NHWC"?(o=e.dims[2],p=e.dims[1],d=e.dims[3]):(o=e.dims[3],p=e.dims[2],d=e.dims[1]);let g=r!==void 0&&r.format!==void 0?r.format:"RGB",m=r==null?void 0:r.norm,_,v;m===void 0||m.mean===void 0?_=[255,255,255,255]:typeof m.mean=="number"?_=[m.mean,m.mean,m.mean,m.mean]:(_=[m.mean[0],m.mean[1],m.mean[2],255],m.mean[3]!==void 0&&(_[3]=m.mean[3])),m===void 0||m.bias===void 0?v=[0,0,0,0]:typeof m.bias=="number"?v=[m.bias,m.bias,m.bias,m.bias]:(v=[m.bias[0],m.bias[1],m.bias[2],0],m.bias[3]!==void 0&&(v[3]=m.bias[3]));let x=p*o;if(r!==void 0&&(r.format!==void 0&&d===4&&r.format!=="RGBA"||d===3&&r.format!=="RGB"&&r.format!=="BGR"))throw new Error("Tensor format doesn't match input tensor dims");let T=4,C=0,A=1,R=2,H=3,U=0,P=x,F=x*2,G=-1;g==="RGBA"?(U=0,P=x,F=x*2,G=x*3):g==="RGB"?(U=0,P=x,F=x*2):g==="RBG"&&(U=0,F=x,P=x*2),s=a.createImageData(o,p);for(let K=0;K<p*o;C+=T,A+=T,R+=T,H+=T,K++)s.data[C]=(e.data[U++]-v[0])*_[0],s.data[A]=(e.data[P++]-v[1])*_[1],s.data[R]=(e.data[F++]-v[2])*_[2],s.data[H]=G===-1?255:(e.data[G++]-v[3])*_[3]}else throw new Error("Can not access image data");return s}}),Uu,Zw,Qw,Xw,Yw,Jw,N2=Ee(()=>{xp(),Uu=(e,r)=>{if(e===void 0)throw new Error("Image buffer must be defined");if(r.height===void 0||r.width===void 0)throw new Error("Image height and width must be defined");if(r.tensorLayout==="NHWC")throw new Error("NHWC Tensor layout is not supported yet");let{height:a,width:s}=r,o=r.norm??{mean:255,bias:0},p,d;typeof o.mean=="number"?p=[o.mean,o.mean,o.mean,o.mean]:p=[o.mean[0],o.mean[1],o.mean[2],o.mean[3]??255],typeof o.bias=="number"?d=[o.bias,o.bias,o.bias,o.bias]:d=[o.bias[0],o.bias[1],o.bias[2],o.bias[3]??0];let g=r.format!==void 0?r.format:"RGBA",m=r.tensorFormat!==void 0&&r.tensorFormat!==void 0?r.tensorFormat:"RGB",_=a*s,v=m==="RGBA"?new Float32Array(_*4):new Float32Array(_*3),x=4,T=0,C=1,A=2,R=3,H=0,U=_,P=_*2,F=-1;g==="RGB"&&(x=3,T=0,C=1,A=2,R=-1),m==="RGBA"?F=_*3:m==="RBG"?(H=0,P=_,U=_*2):m==="BGR"&&(P=0,U=_,H=_*2);for(let G=0;G<_;G++,T+=x,A+=x,C+=x,R+=x)v[H++]=(e[T]+d[0])/p[0],v[U++]=(e[C]+d[1])/p[1],v[P++]=(e[A]+d[2])/p[2],F!==-1&&R!==-1&&(v[F++]=(e[R]+d[3])/p[3]);return m==="RGBA"?new ni("float32",v,[1,4,a,s]):new ni("float32",v,[1,3,a,s])},Zw=async(e,r)=>{let a=typeof HTMLImageElement<"u"&&e instanceof HTMLImageElement,s=typeof ImageData<"u"&&e instanceof ImageData,o=typeof ImageBitmap<"u"&&e instanceof ImageBitmap,p=typeof e=="string",d,g=r??{},m=()=>{if(typeof document<"u")return document.createElement("canvas");if(typeof OffscreenCanvas<"u")return new OffscreenCanvas(1,1);throw new Error("Canvas is not supported")},_=v=>typeof HTMLCanvasElement<"u"&&v instanceof HTMLCanvasElement||v instanceof OffscreenCanvas?v.getContext("2d"):null;if(a){let v=m();v.width=e.width,v.height=e.height;let x=_(v);if(x!=null){let T=e.height,C=e.width;if(r!==void 0&&r.resizedHeight!==void 0&&r.resizedWidth!==void 0&&(T=r.resizedHeight,C=r.resizedWidth),r!==void 0){if(g=r,r.tensorFormat!==void 0)throw new Error("Image input config format must be RGBA for HTMLImageElement");g.tensorFormat="RGBA",g.height=T,g.width=C}else g.tensorFormat="RGBA",g.height=T,g.width=C;x.drawImage(e,0,0),d=x.getImageData(0,0,C,T).data}else throw new Error("Can not access image data")}else if(s){let v,x;if(r!==void 0&&r.resizedWidth!==void 0&&r.resizedHeight!==void 0?(v=r.resizedHeight,x=r.resizedWidth):(v=e.height,x=e.width),r!==void 0&&(g=r),g.format="RGBA",g.height=v,g.width=x,r!==void 0){let T=m();T.width=x,T.height=v;let C=_(T);if(C!=null)C.putImageData(e,0,0),d=C.getImageData(0,0,x,v).data;else throw new Error("Can not access image data")}else d=e.data}else if(o){if(r===void 0)throw new Error("Please provide image config with format for Imagebitmap");let v=m();v.width=e.width,v.height=e.height;let x=_(v);if(x!=null){let T=e.height,C=e.width;return x.drawImage(e,0,0,C,T),d=x.getImageData(0,0,C,T).data,g.height=T,g.width=C,Uu(d,g)}else throw new Error("Can not access image data")}else{if(p)return new Promise((v,x)=>{let T=m(),C=_(T);if(!e||!C)return x();let A=new Image;A.crossOrigin="Anonymous",A.src=e,A.onload=()=>{T.width=A.width,T.height=A.height,C.drawImage(A,0,0,T.width,T.height);let R=C.getImageData(0,0,T.width,T.height);g.height=T.height,g.width=T.width,v(Uu(R.data,g))}});throw new Error("Input data provided is not supported - aborted tensor creation")}if(d!==void 0)return Uu(d,g);throw new Error("Input data provided is not supported - aborted tensor creation")},Qw=(e,r)=>{let{width:a,height:s,download:o,dispose:p}=r,d=[1,s,a,4];return new ni({location:"texture",type:"float32",texture:e,dims:d,download:o,dispose:p})},Xw=(e,r)=>{let{dataType:a,dims:s,download:o,dispose:p}=r;return new ni({location:"gpu-buffer",type:a??"float32",gpuBuffer:e,dims:s,download:o,dispose:p})},Yw=(e,r)=>{let{dataType:a,dims:s,download:o,dispose:p}=r;return new ni({location:"ml-tensor",type:a??"float32",mlTensor:e,dims:s,download:o,dispose:p})},Jw=(e,r,a)=>new ni({location:"cpu-pinned",type:e,data:r,dims:a??[r.length]})}),Nn,Uo,ld,e0,P2=Ee(()=>{Nn=new Map([["float32",Float32Array],["uint8",Uint8Array],["int8",Int8Array],["uint16",Uint16Array],["int16",Int16Array],["int32",Int32Array],["bool",Uint8Array],["float64",Float64Array],["uint32",Uint32Array],["int4",Uint8Array],["uint4",Uint8Array]]),Uo=new Map([[Float32Array,"float32"],[Uint8Array,"uint8"],[Int8Array,"int8"],[Uint16Array,"uint16"],[Int16Array,"int16"],[Int32Array,"int32"],[Float64Array,"float64"],[Uint32Array,"uint32"]]),ld=!1,e0=()=>{if(!ld){ld=!0;let e=typeof BigInt64Array<"u"&&BigInt64Array.from,r=typeof BigUint64Array<"u"&&BigUint64Array.from,a=globalThis.Float16Array,s=typeof a<"u"&&a.from;e&&(Nn.set("int64",BigInt64Array),Uo.set(BigInt64Array,"int64")),r&&(Nn.set("uint64",BigUint64Array),Uo.set(BigUint64Array,"uint64")),s?(Nn.set("float16",a),Uo.set(a,"float16")):Nn.set("float16",Uint16Array)}}}),t0,r0,U2=Ee(()=>{xp(),t0=e=>{let r=1;for(let a=0;a<e.length;a++){let s=e[a];if(typeof s!="number"||!Number.isSafeInteger(s))throw new TypeError(`dims[${a}] must be an integer, got: ${s}`);if(s<0)throw new RangeError(`dims[${a}] must be a non-negative integer, got: ${s}`);r*=s}return r},r0=(e,r)=>{switch(e.location){case"cpu":return new ni(e.type,e.data,r);case"cpu-pinned":return new ni({location:"cpu-pinned",data:e.data,type:e.type,dims:r});case"texture":return new ni({location:"texture",texture:e.texture,type:e.type,dims:r});case"gpu-buffer":return new ni({location:"gpu-buffer",gpuBuffer:e.gpuBuffer,type:e.type,dims:r});case"ml-tensor":return new ni({location:"ml-tensor",mlTensor:e.mlTensor,type:e.type,dims:r});default:throw new Error(`tensorReshape: tensor location ${e.location} is not supported`)}}}),ni,xp=Ee(()=>{D2(),N2(),P2(),U2(),ni=class{constructor(e,r,a){e0();let s,o;if(typeof e=="object"&&"location"in e)switch(this.dataLocation=e.location,s=e.type,o=e.dims,e.location){case"cpu-pinned":{let d=Nn.get(s);if(!d)throw new TypeError(`unsupported type "${s}" to create tensor from pinned buffer`);if(!(e.data instanceof d))throw new TypeError(`buffer should be of type ${d.name}`);this.cpuData=e.data;break}case"texture":{if(s!=="float32")throw new TypeError(`unsupported type "${s}" to create tensor from texture`);this.gpuTextureData=e.texture,this.downloader=e.download,this.disposer=e.dispose;break}case"gpu-buffer":{if(s!=="float32"&&s!=="float16"&&s!=="int32"&&s!=="int64"&&s!=="uint32"&&s!=="uint8"&&s!=="bool"&&s!=="uint4"&&s!=="int4")throw new TypeError(`unsupported type "${s}" to create tensor from gpu buffer`);this.gpuBufferData=e.gpuBuffer,this.downloader=e.download,this.disposer=e.dispose;break}case"ml-tensor":{if(s!=="float32"&&s!=="float16"&&s!=="int32"&&s!=="int64"&&s!=="uint32"&&s!=="uint64"&&s!=="int8"&&s!=="uint8"&&s!=="bool"&&s!=="uint4"&&s!=="int4")throw new TypeError(`unsupported type "${s}" to create tensor from MLTensor`);this.mlTensorData=e.mlTensor,this.downloader=e.download,this.disposer=e.dispose;break}default:throw new Error(`Tensor constructor: unsupported location '${this.dataLocation}'`)}else{let d,g;if(typeof e=="string")if(s=e,g=a,e==="string"){if(!Array.isArray(r))throw new TypeError("A string tensor's data must be a string array.");d=r}else{let m=Nn.get(e);if(m===void 0)throw new TypeError(`Unsupported tensor type: ${e}.`);if(Array.isArray(r)){if(e==="float16"&&m===Uint16Array||e==="uint4"||e==="int4")throw new TypeError(`Creating a ${e} tensor from number array is not supported. Please use ${m.name} as data.`);e==="uint64"||e==="int64"?d=m.from(r,BigInt):d=m.from(r)}else if(r instanceof m)d=r;else if(r instanceof Uint8ClampedArray)if(e==="uint8")d=Uint8Array.from(r);else throw new TypeError("A Uint8ClampedArray tensor's data must be type of uint8");else if(e==="float16"&&r instanceof Uint16Array&&m!==Uint16Array)d=new globalThis.Float16Array(r.buffer,r.byteOffset,r.length);else throw new TypeError(`A ${s} tensor's data must be type of ${m}`)}else if(g=r,Array.isArray(e)){if(e.length===0)throw new TypeError("Tensor type cannot be inferred from an empty array.");let m=typeof e[0];if(m==="string")s="string",d=e;else if(m==="boolean")s="bool",d=Uint8Array.from(e);else throw new TypeError(`Invalid element type of data array: ${m}.`)}else if(e instanceof Uint8ClampedArray)s="uint8",d=Uint8Array.from(e);else{let m=Uo.get(e.constructor);if(m===void 0)throw new TypeError(`Unsupported type for tensor data: ${e.constructor}.`);s=m,d=e}if(g===void 0)g=[d.length];else if(!Array.isArray(g))throw new TypeError("A tensor's dims must be a number array");o=g,this.cpuData=d,this.dataLocation="cpu"}let p=t0(o);if(this.cpuData&&p!==this.cpuData.length&&!((s==="uint4"||s==="int4")&&Math.ceil(p/2)===this.cpuData.length))throw new Error(`Tensor's size(${p}) does not match data length(${this.cpuData.length}).`);this.type=s,this.dims=o,this.size=p}static async fromImage(e,r){return Zw(e,r)}static fromTexture(e,r){return Qw(e,r)}static fromGpuBuffer(e,r){return Xw(e,r)}static fromMLTensor(e,r){return Yw(e,r)}static fromPinnedBuffer(e,r,a){return Jw(e,r,a)}toDataURL(e){return jw(this,e)}toImageData(e){return Kw(this,e)}get data(){if(this.ensureValid(),!this.cpuData)throw new Error("The data is not on CPU. Use `getData()` to download GPU data to CPU, or use `texture` or `gpuBuffer` property to access the GPU data directly.");return this.cpuData}get location(){return this.dataLocation}get texture(){if(this.ensureValid(),!this.gpuTextureData)throw new Error("The data is not stored as a WebGL texture.");return this.gpuTextureData}get gpuBuffer(){if(this.ensureValid(),!this.gpuBufferData)throw new Error("The data is not stored as a WebGPU buffer.");return this.gpuBufferData}get mlTensor(){if(this.ensureValid(),!this.mlTensorData)throw new Error("The data is not stored as a WebNN MLTensor.");return this.mlTensorData}async getData(e){switch(this.ensureValid(),this.dataLocation){case"cpu":case"cpu-pinned":return this.data;case"texture":case"gpu-buffer":case"ml-tensor":{if(!this.downloader)throw new Error("The current tensor is not created with a specified data downloader.");if(this.isDownloading)throw new Error("The current tensor is being downloaded.");try{this.isDownloading=!0;let r=await this.downloader();return this.downloader=void 0,this.dataLocation="cpu",this.cpuData=r,e&&this.disposer&&(this.disposer(),this.disposer=void 0),r}finally{this.isDownloading=!1}}default:throw new Error(`cannot get data from location: ${this.dataLocation}`)}}dispose(){if(this.isDownloading)throw new Error("The current tensor is being downloaded.");this.disposer&&(this.disposer(),this.disposer=void 0),this.cpuData=void 0,this.gpuTextureData=void 0,this.gpuBufferData=void 0,this.mlTensorData=void 0,this.downloader=void 0,this.isDownloading=void 0,this.dataLocation="none"}ensureValid(){if(this.dataLocation==="none")throw new Error("The tensor is disposed.")}reshape(e){if(this.ensureValid(),this.downloader||this.disposer)throw new Error("Cannot reshape a tensor that owns GPU resource.");return r0(this,e)}}}),Qi,i0=Ee(()=>{xp(),Qi=ni}),tl,dd,Xi,Pi,Ln,qn,a0=Ee(()=>{Hw(),tl=(e,r)=>{(typeof Sr.trace>"u"?!Sr.wasm.trace:!Sr.trace)||console.timeStamp(`${e}::ORT::${r}`)},dd=(e,r)=>{var o;let a=((o=new Error().stack)==null?void 0:o.split(/\r\n|\r|\n/g))||[],s=!1;for(let p=0;p<a.length;p++){if(s&&!a[p].includes("TRACE_FUNC")){let d=`FUNC_${e}::${a[p].trim().split(" ")[1]}`;r&&(d+=`::${r}`),tl("CPU",d);return}a[p].includes("TRACE_FUNC")&&(s=!0)}},Xi=e=>{(typeof Sr.trace>"u"?!Sr.wasm.trace:!Sr.trace)||dd("BEGIN",e)},Pi=e=>{(typeof Sr.trace>"u"?!Sr.wasm.trace:!Sr.trace)||dd("END",e)},Ln=e=>{(typeof Sr.trace>"u"?!Sr.wasm.trace:!Sr.trace)||console.time(`ORT::${e}`)},qn=e=>{(typeof Sr.trace>"u"?!Sr.wasm.trace:!Sr.trace)||console.timeEnd(`ORT::${e}`)}}),n0,L2=Ee(()=>{Gw(),i0(),a0(),n0=class s0{constructor(r){this.handler=r}async run(r,a,s){Xi(),Ln("InferenceSession.run");let o={},p={};if(typeof r!="object"||r===null||r instanceof Qi||Array.isArray(r))throw new TypeError("'feeds' must be an object that use input names as keys and OnnxValue as corresponding values.");let d=!0;if(typeof a=="object"){if(a===null)throw new TypeError("Unexpected argument[1]: cannot be null.");if(a instanceof Qi)throw new TypeError("'fetches' cannot be a Tensor");if(Array.isArray(a)){if(a.length===0)throw new TypeError("'fetches' cannot be an empty array.");d=!1;for(let _ of a){if(typeof _!="string")throw new TypeError("'fetches' must be a string array or an object.");if(this.outputNames.indexOf(_)===-1)throw new RangeError(`'fetches' contains invalid output name: ${_}.`);o[_]=null}if(typeof s=="object"&&s!==null)p=s;else if(typeof s<"u")throw new TypeError("'options' must be an object.")}else{let _=!1,v=Object.getOwnPropertyNames(a);for(let x of this.outputNames)if(v.indexOf(x)!==-1){let T=a[x];(T===null||T instanceof Qi)&&(_=!0,d=!1,o[x]=T)}if(_){if(typeof s=="object"&&s!==null)p=s;else if(typeof s<"u")throw new TypeError("'options' must be an object.")}else p=a}}else if(typeof a<"u")throw new TypeError("Unexpected argument[1]: must be 'fetches' or 'options'.");for(let _ of this.inputNames)if(typeof r[_]>"u")throw new Error(`input '${_}' is missing in 'feeds'.`);if(d)for(let _ of this.outputNames)o[_]=null;let g=await this.handler.run(r,o,p),m={};for(let _ in g)if(Object.hasOwnProperty.call(g,_)){let v=g[_];v instanceof Qi?m[_]=v:m[_]=new Qi(v.type,v.data,v.dims)}return qn("InferenceSession.run"),Pi(),m}async release(){return this.handler.dispose()}static async create(r,a,s,o){Xi(),Ln("InferenceSession.create");let p,d={};if(typeof r=="string"){if(p=r,typeof a=="object"&&a!==null)d=a;else if(typeof a<"u")throw new TypeError("'options' must be an object.")}else if(r instanceof Uint8Array){if(p=r,typeof a=="object"&&a!==null)d=a;else if(typeof a<"u")throw new TypeError("'options' must be an object.")}else if(r instanceof ArrayBuffer||typeof SharedArrayBuffer<"u"&&r instanceof SharedArrayBuffer){let v=r,x=0,T=r.byteLength;if(typeof a=="object"&&a!==null)d=a;else if(typeof a=="number"){if(x=a,!Number.isSafeInteger(x))throw new RangeError("'byteOffset' must be an integer.");if(x<0||x>=v.byteLength)throw new RangeError(`'byteOffset' is out of range [0, ${v.byteLength}).`);if(T=r.byteLength-x,typeof s=="number"){if(T=s,!Number.isSafeInteger(T))throw new RangeError("'byteLength' must be an integer.");if(T<=0||x+T>v.byteLength)throw new RangeError(`'byteLength' is out of range (0, ${v.byteLength-x}].`);if(typeof o=="object"&&o!==null)d=o;else if(typeof o<"u")throw new TypeError("'options' must be an object.")}else if(typeof s<"u")throw new TypeError("'byteLength' must be a number.")}else if(typeof a<"u")throw new TypeError("'options' must be an object.");p=new Uint8Array(v,x,T)}else throw new TypeError("Unexpected argument[0]: must be 'path' or 'buffer'.");let[g,m]=await Ww(d),_=await g.createInferenceSessionHandler(p,m);return qn("InferenceSession.create"),Pi(),new s0(_)}startProfiling(){this.handler.startProfiling()}endProfiling(){this.handler.endProfiling()}get inputNames(){return this.handler.inputNames}get outputNames(){return this.handler.outputNames}get inputMetadata(){return this.handler.inputMetadata}get outputMetadata(){return this.handler.outputMetadata}}}),o0,q2=Ee(()=>{L2(),o0=n0}),V2=Ee(()=>{}),W2=Ee(()=>{}),G2=Ee(()=>{}),F2=Ee(()=>{}),H2={};ys(H2,{InferenceSession:()=>o0,TRACE:()=>tl,TRACE_EVENT_BEGIN:()=>Ln,TRACE_EVENT_END:()=>qn,TRACE_FUNC_BEGIN:()=>Xi,TRACE_FUNC_END:()=>Pi,Tensor:()=>Qi,env:()=>Ft,registerBackend:()=>hs});var bi=Ee(()=>{R2(),M2(),q2(),i0(),V2(),W2(),a0(),G2(),F2()}),Sp=Ee(()=>{}),u0={};ys(u0,{default:()=>l0});var pd,cd,l0,j2=Ee(()=>{var e;fv(),Fn(),Tp(),pd="ort-wasm-proxy-worker",cd=((e=globalThis.self)==null?void 0:e.name)===pd,cd&&(self.onmessage=r=>{let{type:a,in:s}=r.data;try{switch(a){case"init-wasm":kp(s.wasm).then(()=>{Wp(s).then(()=>{postMessage({type:a})},o=>{postMessage({type:a,err:o})})},o=>{postMessage({type:a,err:o})});break;case"init-ep":{let{epName:o,env:p}=s;Gp(p,o).then(()=>{postMessage({type:a})},d=>{postMessage({type:a,err:d})});break}case"copy-from":{let{buffer:o}=s,p=ul(o);postMessage({type:a,out:p});break}case"create":{let{model:o,options:p}=s;Fp(o,p).then(d=>{postMessage({type:a,out:d})},d=>{postMessage({type:a,err:d})});break}case"release":Hp(s),postMessage({type:a});break;case"run":{let{sessionId:o,inputIndices:p,inputs:d,outputIndices:g,options:m}=s;jp(o,p,d,g,new Array(g.length).fill(null),m).then(_=>{_.some(v=>v[3]!=="cpu")?postMessage({type:a,err:"Proxy does not support non-cpu tensor location."}):postMessage({type:a,out:_},Zp([...d,..._]))},_=>{postMessage({type:a,err:_})});break}case"end-profiling":Kp(s),postMessage({type:a});break;default:}}catch(o){postMessage({type:a,err:o})}}),l0=cd?null:r=>new Worker(r??ai,{type:"module",name:pd})}),d0={};ys(d0,{default:()=>p0});async function ag(e={}){var lo,po;var r=e,a=!!globalThis.window,s=!!globalThis.WorkerGlobalScope,o=s&&((lo=self.name)==null?void 0:lo.startsWith("em-pthread"));r.mountExternalData=(f,w)=>{f.startsWith("./")&&(f=f.substring(2)),(r.Xc||(r.Xc=new Map)).set(f,w)},r.unmountExternalData=()=>{delete r.Xc},globalThis.SharedArrayBuffer??new WebAssembly.Memory({initial:0,maximum:0,shared:!0}).buffer.constructor;let p=f=>async(...w)=>{var E;try{if(r.Yc)throw Error("Session already started");let S=r.Yc={Kd:w[0],errors:[]},q=await f(...w);if(r.Yc!==S)throw Error("Session mismatch");(E=r.dd)==null||E.flush();let Q=S.errors;if(0<Q.length){let ne=await Promise.all(Q);if(ne=ne.filter(ye=>ye),0<ne.length)throw Error(ne.join(`
`))}return q}finally{r.Yc=null}};r.jsepInit=(f,w)=>{if(f==="webgpu"){[r.dd,r.Ad,r.Ed,r.ed,r.Dd,r.$b,r.Fd,r.Hd,r.Bd,r.Cd,r.Gd]=w;let E=r.dd;r.jsepRegisterBuffer=(S,q,Q,ne)=>E.registerBuffer(S,q,Q,ne),r.jsepGetBuffer=S=>E.getBuffer(S),r.jsepCreateDownloader=(S,q,Q)=>E.createDownloader(S,q,Q),r.jsepOnCreateSession=S=>{E.onCreateSession(S)},r.jsepOnReleaseSession=S=>{E.onReleaseSession(S)},r.jsepOnRunStart=S=>E.onRunStart(S),r.Id=(S,q)=>{E.upload(S,q)}}else if(f==="webnn"){let E=w[0];[r.Wd,r.sd,r.webnnEnsureTensor,r.td,r.webnnDownloadTensor,r.Rd,r.webnnEnableTraceEvent]=w.slice(1),r.webnnReleaseTensorId=r.sd,r.webnnUploadTensor=r.td,r.webnnRegisterMLContext=r.Rd,r.webnnOnRunStart=S=>E.onRunStart(S),r.webnnOnRunEnd=E.onRunEnd.bind(E),r.webnnOnReleaseSession=S=>{E.onReleaseSession(S)},r.webnnCreateMLTensorDownloader=(S,q)=>E.createMLTensorDownloader(S,q),r.webnnRegisterMLTensor=(S,q,Q,ne)=>E.registerMLTensor(S,q,Q,ne),r.webnnCreateMLContext=S=>E.createMLContext(S),r.webnnRegisterMLConstant=(S,q,Q,ne,ye,Oe)=>E.registerMLConstant(S,q,Q,ne,ye,r.Xc,Oe),r.webnnRegisterGraphInput=E.registerGraphInput.bind(E),r.webnnIsGraphInput=E.isGraphInput.bind(E),r.webnnRegisterGraphOutput=E.registerGraphOutput.bind(E),r.webnnIsGraphOutput=E.isGraphOutput.bind(E),r.webnnCreateTemporaryTensor=E.createTemporaryTensor.bind(E),r.webnnIsGraphInputOutputTypeSupported=E.isGraphInputOutputTypeSupported.bind(E)}};let d=()=>{let f=w=>(...E)=>{let S=Cr;return E=w(...E),Cr!=S?new Promise((q,Q)=>{Hi={resolve:q,reject:Q}}):E};(()=>{for(let w of["_OrtAppendExecutionProvider","_OrtCreateSession","_OrtRun","_OrtRunWithBinding","_OrtBindInput"])r[w]=f(r[w])})(),p!==void 0&&(r._OrtRun=p(r._OrtRun),r._OrtRunWithBinding=p(r._OrtRunWithBinding)),d=void 0};r.asyncInit=()=>{d==null||d()};var g,m,_=(f,w)=>{throw w},v=import.meta.url,x="";if(a||s){try{x=new URL(".",v).href}catch{}s&&(m=f=>{var w=new XMLHttpRequest;return w.open("GET",f,!1),w.responseType="arraybuffer",w.send(null),new Uint8Array(w.response)}),g=async f=>{if(ae(f))return new Promise((E,S)=>{var q=new XMLHttpRequest;q.open("GET",f,!0),q.responseType="arraybuffer",q.onload=()=>{q.status==200||q.status==0&&q.response?E(q.response):S(q.status)},q.onerror=S,q.send(null)});var w=await fetch(f,{credentials:"same-origin"});if(w.ok)return w.arrayBuffer();throw Error(w.status+" : "+w.url)}}var T,C,A,R,H,U,P=console.log.bind(console),F=console.error.bind(console),G=P,K=F,ee=!1,ae=f=>f.startsWith("file://");function B(){oi.buffer!=_e.buffer&&Be()}if(o){let f=function(w){try{var E=w.data,S=E.Sc;if(S==="load"){let q=[];self.onmessage=Q=>q.push(Q),U=()=>{postMessage({Sc:"loaded"});for(let Q of q)f(Q);self.onmessage=f};for(let Q of E.xd)r[Q]&&!r[Q].proxy||(r[Q]=(...ne)=>{postMessage({Sc:"callHandler",wd:Q,args:ne})},Q=="print"&&(G=r[Q]),Q=="printErr"&&(K=r[Q]));oi=E.Od,Be(),C=E.Pd,_r(),Tn()}else if(S==="run"){(function(q){var Q=(B(),qe)[q+52>>>2>>>0];q=(B(),qe)[q+56>>>2>>>0],Qr(Q,Q-q),gt(Q)})(E.Rc),ts(E.Rc,0,0,1,0,0),Zn(),jr(E.Rc),me||(xn(),me=!0);try{sn(E.Md,E.bd)}catch(q){if(q!="unwind")throw q}}else E.target!=="setimmediate"&&(S==="checkMailbox"?me&&Jr():S&&(K(`worker: received unknown command ${S}`),K(E)))}catch(q){throw $s(),q}};var me=!1;self.onunhandledrejection=w=>{throw w.reason||w},self.onmessage=f}var _e,Re,Ue,Me,pe,qe,Ve,ze,ht,Ce,nt,Te=!1;function Be(){var f=oi.buffer;r.HEAP8=_e=new Int8Array(f),Ue=new Int16Array(f),r.HEAPU8=Re=new Uint8Array(f),Me=new Uint16Array(f),r.HEAP32=pe=new Int32Array(f),r.HEAPU32=qe=new Uint32Array(f),Ve=new Float32Array(f),ze=new Float64Array(f),ht=new BigInt64Array(f),Ce=new BigUint64Array(f)}function We(){Te=!0,o?U():_i.sb()}function Ie(f){throw K(f="Aborted("+f+")"),ee=!0,f=new WebAssembly.RuntimeError(f+". Build with -sASSERTIONS for more info."),H==null||H(f),f}function $t(){return{a:{ma:ds,gb:Yo,g:on,J:Yi,f:Ut,o:ta,h:vi,ha:un,b:ln,T:ra,Ha:Ui,n:dn,$:li,Xa:Fr,Da:Vi,Fa:za,Ya:Ca,Va:Aa,Oa:Wi,Ua:st,ka:kr,Ea:Ht,Ba:di,Wa:Ye,Ca:Gi,bb:pn,ea:cn,wa:_s,ua:Ra,da:sa,O:hn,H:fn,va:ci,_:Fi,xa:Da,Ra:mn,za:Ti,Ia:ua,sa:la,fa:ei,Qa:jr,_a:mi,R:ca,r:ws,c:wr,hb:z,y:N,M:j,D:te,l:X,s:ue,ib:re,I:ie,S:le,j:ve,u:Se,q:be,k:ce,La:Fe,Ma:dt,Na:lt,Ja:Mt,Ka:Ke,ta:ir,db:mr,ab:fa,v:wn,aa:or,ga:Kr,$a:br,W:bn,Za:wt,Aa:Nr,F:vt,U:Jn,la:Ei,ya:vr,fb:$r,eb:$n,Sa:Ii,Ta:Xt,Ga:tr,V:Gt,ja:Ur,Pa:vn,ia:jo,kb:Iu,na:pl,lb:Eu,oa:Su,G:gu,d:ru,t:eu,w:Jo,A:cu,mb:uo,K:mu,x:nu,pa:vu,Y:Tu,ba:$u,nb:bu,ob:wu,P:hu,qa:_u,pb:yu,N:so,Z:xu,e:tu,B:au,m:iu,jb:zu,p:ou,z:uu,C:su,E:lu,L:fu,qb:oo,Q:ku,ca:no,X:ri,rb:pu,ra:du,i:ti,a:oi,cb:Rt}}}async function _r(){function f(S,q){var Q=_i=S.exports;S={};for(let[ne,ye]of Object.entries(Q))typeof ye=="function"?(Q=Na(ye),S[ne]=Q):S[ne]=ye;return _i=S,_i=(function(){var ne=_i,ye=Le=>pt=>Le(pt)>>>0,Oe=Le=>()=>Le()>>>0;return(ne=Object.assign({},ne)).tb=ye(ne.tb),ne.Xb=Oe(ne.Xb),ne.Zb=ye(ne.Zb),ne.lc=ye(ne.lc),ne.mc=Oe(ne.mc),ne.qc=ye(ne.qc),ne})(),jn.push(_i._b),bs=(S=_i).tb,xn=S.ub,r._OrtInit=S.vb,r._OrtGetLastError=S.wb,r._OrtCreateSessionOptions=S.xb,r._OrtAppendExecutionProvider=S.yb,r._OrtAddFreeDimensionOverride=S.zb,r._OrtAddSessionConfigEntry=S.Ab,r._OrtReleaseSessionOptions=S.Bb,r._OrtCreateSession=S.Cb,r._OrtReleaseSession=S.Db,r._OrtGetInputOutputCount=S.Eb,r._OrtGetInputOutputMetadata=S.Fb,r._OrtFree=S.Gb,r._OrtCreateTensor=S.Hb,r._OrtGetTensorData=S.Ib,r._OrtReleaseTensor=S.Jb,r._OrtCreateRunOptions=S.Kb,r._OrtAddRunConfigEntry=S.Lb,r._OrtReleaseRunOptions=S.Mb,r._OrtCreateBinding=S.Nb,r._OrtBindInput=S.Ob,r._OrtBindOutput=S.Pb,r._OrtClearBoundOutputs=S.Qb,r._OrtReleaseBinding=S.Rb,r._OrtRunWithBinding=S.Sb,r._OrtRun=S.Tb,r._OrtEndProfiling=S.Ub,r._JsepOutput=S.Vb,r._JsepGetNodeName=S.Wb,Ha=S.Xb,ar=r._free=S.Yb,ja=r._malloc=S.Zb,ts=S.ac,$s=S.bc,vs=S.cc,xs=S.dc,rs=S.ec,Ss=S.fc,Ts=S.gc,bt=S.hc,Ka=S.ic,Qr=S.jc,gt=S.kc,is=S.lc,yt=S.mc,ks=S.nc,as=S.oc,Es=S.pc,Is=S.qc,zs=S.rc,ns=S.sc,Cs=S.tc,ss=S.uc,os=S.vc,As=S.wc,Os=S.xc,us=S.yc,Ko=S.zc,Rs=S.Ac,Sn=S.Bc,Bs=S.Cc,Ms=S.Dc,Ds=S.Ec,Za=S.Fc,Ns=S.Gc,Ps=S.Hc,ls=S.Ic,Us=S.Jc,Ls=S.Kc,qs=S.Lc,Vs=S.Mc,Zo=S.Nc,Ws=S.Pc,Gs=S.Qc,Fs=S.$c,Qo=S.ad,Hs=S.fd,Tt=S.jd,js=S.kd,Ks=S.ld,Zs=S.md,Qs=S.nd,Xs=S.od,Ys=S.pd,Js=S.qd,eo=S.vd,to=S.Sd,ro=S.Td,io=S.Ud,ao=S.Vd,C=q,_i}var w,E=$t();return r.instantiateWasm?new Promise(S=>{r.instantiateWasm(E,(q,Q)=>{S(f(q,Q))})}):o?f(new WebAssembly.Instance(C,$t()),C):(nt??(nt=r.locateFile?r.locateFile?r.locateFile("ort-wasm-simd-threaded.jsep.wasm",x):x+"ort-wasm-simd-threaded.jsep.wasm":new URL("/assets/ort-wasm-simd-threaded.jsep-CyqnNavA.wasm",import.meta.url).href),w=await(async function(S){var q=nt;if(!T&&!ae(q))try{var Q=fetch(q,{credentials:"same-origin"});return await WebAssembly.instantiateStreaming(Q,S)}catch(ne){K(`wasm streaming compile failed: ${ne}`),K("falling back to ArrayBuffer instantiation")}return(async function(ne,ye){try{var Oe=await(async function(Le){if(!T)try{var pt=await g(Le);return new Uint8Array(pt)}catch{}if(Le==nt&&T)Le=new Uint8Array(T);else{if(!m)throw"both async and sync fetching of the wasm failed";Le=m(Le)}return Le})(ne);return await WebAssembly.instantiate(Oe,ye)}catch(Le){K(`failed to asynchronously prepare wasm: ${Le}`),Ie(Le)}})(q,S)})(E),f(w.instance,w.module))}class jt{constructor(w){Um(this,"name","ExitStatus");this.message=`Program terminated with exit(${w})`,this.status=w}}var _t=f=>{f.terminate(),f.onmessage=()=>{}},er=[],St=0,dr=null,hr=f=>{Wr.length==0&&(Xn(),Qn(Wr[0]));var w=Wr.pop();if(!w)return 6;$i.push(w),Rr[f.Rc]=w,w.Rc=f.Rc;var E={Sc:"run",Md:f.Ld,bd:f.bd,Rc:f.Rc};return w.postMessage(E,f.rd),0},Ct=0,He=(f,w,...E)=>{var S,q=16*E.length,Q=yt(),ne=is(q),ye=ne>>>3;for(S of E)typeof S=="bigint"?((B(),ht)[ye++>>>0]=1n,(B(),ht)[ye++>>>0]=S):((B(),ht)[ye++>>>0]=0n,(B(),ze)[ye++>>>0]=S);return f=vs(f,0,q,ne,w),gt(Q),f};function Rt(f){if(o)return He(0,1,f);if(A=f,!(0<Ct)){for(var w of $i)_t(w);for(w of Wr)_t(w);Wr=[],$i=[],Rr={},ee=!0}_(0,new jt(f))}function sr(f){if(o)return He(1,0,f);tr(f)}var tr=f=>{if(A=f,o)throw sr(f),"unwind";Rt(f)},Wr=[],$i=[],jn=[],Rr={},Kn=f=>{var w=f.Rc;delete Rr[w],Wr.push(f),$i.splice($i.indexOf(f),1),f.Rc=0,xs(w)};function Zn(){jn.forEach(f=>f())}var Qn=f=>new Promise(w=>{f.onmessage=q=>{var Q=q.data;if(q=Q.Sc,Q.Zc&&Q.Zc!=Ha()){var ne=Rr[Q.Zc];ne?ne.postMessage(Q,Q.rd):K(`Internal error! Worker sent a message "${q}" to target pthread ${Q.Zc}, but that thread no longer exists!`)}else q==="checkMailbox"?Jr():q==="spawnThread"?hr(Q):q==="cleanupThread"?zr(()=>{Kn(Rr[Q.Nd])}):q==="loaded"?(f.loaded=!0,w(f)):Q.target==="setimmediate"?f.postMessage(Q):q==="uncaughtException"?f.onerror(Q.error):q==="callHandler"?r[Q.wd](...Q.args):q&&K(`worker sent an unknown command ${q}`)},f.onerror=q=>{throw K(`worker sent an error! ${q.filename}:${q.lineno}: ${q.message}`),q};var E,S=[];for(E of[])r.propertyIsEnumerable(E)&&S.push(E);f.postMessage({Sc:"load",xd:S,Od:oi,Pd:C})});function Xn(){var f=new Worker((()=>{let w=URL;return import.meta.url>"file:"&&import.meta.url<"file;"?new w("ort.bundle.min.mjs",import.meta.url):new URL(import.meta.url)})(),{type:"module",workerData:"em-pthread",name:"em-pthread"});Wr.push(f)}var oi,sn=(f,w)=>{Ct=0,f=ns(f,w),0<Ct?A=f:rs(f)},Kt=[],ui=0;function on(f){var w=new Ji(f>>>=0);return(B(),_e)[w.Tc+12>>>0]==0&&(ka(w,!0),ui--),Yn(w,!1),Kt.push(w),Is(f)}var Gr=0,Yi=()=>{bt(0,0);var f=Kt.pop();ks(f.cd),Gr=0};function ka(f,w){w=w?1:0,(B(),_e)[f.Tc+12>>>0]=w}function Yn(f,w){w=w?1:0,(B(),_e)[f.Tc+13>>>0]=w}class Ji{constructor(w){this.cd=w,this.Tc=w-24}}var ea=f=>{var w=Gr;if(!w)return Ka(0),0;var E=new Ji(w);(B(),qe)[E.Tc+16>>>2>>>0]=w;var S=(B(),qe)[E.Tc+4>>>2>>>0];if(!S)return Ka(0),w;for(var q of f){if(q===0||q===S)break;if(Es(q,S,E.Tc+16))return Ka(q),w}return Ka(S),w};function Ut(){return ea([])}function ta(f){return ea([f>>>0])}function vi(f,w,E,S){return ea([f>>>0,w>>>0,E>>>0,S>>>0])}var un=()=>{var f=Kt.pop();f||Ie("no exception to throw");var w=f.cd;throw(B(),_e)[f.Tc+13>>>0]==0&&(Kt.push(f),Yn(f,!0),ka(f,!1),ui++),as(w),Gr=w};function ln(f,w,E){var S=new Ji(f>>>=0);throw w>>>=0,E>>>=0,(B(),qe)[S.Tc+16>>>2>>>0]=0,(B(),qe)[S.Tc+4>>>2>>>0]=w,(B(),qe)[S.Tc+8>>>2>>>0]=E,as(f),ui++,Gr=f}var ra=()=>ui;function Ea(f,w,E,S){return o?He(2,1,f,w,E,S):Ui(f,w,E,S)}function Ui(f,w,E,S){if(f>>>=0,w>>>=0,E>>>=0,S>>>=0,!globalThis.SharedArrayBuffer)return 6;var q=[];return o&&q.length===0?Ea(f,w,E,S):(f={Ld:E,Rc:f,bd:S,rd:q},o?(f.Sc="spawnThread",postMessage(f,q),0):hr(f))}function dn(f){throw Gr||(Gr=f>>>0),Gr}var Li=globalThis.TextDecoder&&new TextDecoder,Ia=(f,w,E,S)=>{if(E=w+E,S)return E;for(;f[w]&&!(w>=E);)++w;return w},qi=(f,w=0,E,S)=>{if(16<(E=Ia(f,w>>>=0,E,S))-w&&f.buffer&&Li)return Li.decode(f.buffer instanceof ArrayBuffer?f.subarray(w,E):f.slice(w,E));for(S="";w<E;){var q=f[w++];if(128&q){var Q=63&f[w++];if((224&q)==192)S+=String.fromCharCode((31&q)<<6|Q);else{var ne=63&f[w++];65536>(q=(240&q)==224?(15&q)<<12|Q<<6|ne:(7&q)<<18|Q<<12|ne<<6|63&f[w++])?S+=String.fromCharCode(q):(q-=65536,S+=String.fromCharCode(55296|q>>10,56320|1023&q))}}else S+=String.fromCharCode(q)}return S},Lt=(f,w,E)=>(f>>>=0)?qi((B(),Re),f,w,E):"";function li(f,w,E){return o?He(3,1,f,w,E):0}function Fr(f,w){if(o)return He(4,1,f,w)}function Vi(f,w){if(o)return He(5,1,f,w)}function za(f,w,E){if(o)return He(6,1,f,w,E)}function Ca(f,w,E){return o?He(7,1,f,w,E):0}function Aa(f,w){if(o)return He(8,1,f,w)}function Wi(f,w,E){if(o)return He(9,1,f,w,E)}function st(f,w,E,S){if(o)return He(10,1,f,w,E,S)}function kr(f,w,E,S){if(o)return He(11,1,f,w,E,S)}function Ht(f,w,E,S){if(o)return He(12,1,f,w,E,S)}function di(f){if(o)return He(13,1,f)}function Ye(f,w){if(o)return He(14,1,f,w)}function Gi(f,w,E){if(o)return He(15,1,f,w,E)}var pn=()=>Ie(""),Hr=f=>{f>>>=0;for(var w="";;){var E=(B(),Re)[f++>>>0];if(!E)return w;w+=String.fromCharCode(E)}},ia={},aa={},pi=class extends Error{constructor(f){super(f),this.name="BindingError"}};function Zt(f,w,E={}){return(function(S,q,Q={}){var ne=q.name;if(!S)throw new pi(`type "${ne}" must have a positive integer typeid pointer`);if(aa.hasOwnProperty(S)){if(Q.yd)return;throw new pi(`Cannot register type '${ne}' twice`)}aa[S]=q,ia.hasOwnProperty(S)&&(q=ia[S],delete ia[S],q.forEach(ye=>ye()))})(f,w,E)}var Oa=(f,w,E)=>{switch(w){case 1:return E?S=>(B(),_e)[S>>>0]:S=>(B(),Re)[S>>>0];case 2:return E?S=>(B(),Ue)[S>>>1>>>0]:S=>(B(),Me)[S>>>1>>>0];case 4:return E?S=>(B(),pe)[S>>>2>>>0]:S=>(B(),qe)[S>>>2>>>0];case 8:return E?S=>(B(),ht)[S>>>3>>>0]:S=>(B(),Ce)[S>>>3>>>0];default:throw new TypeError(`invalid integer width (${w}): ${f}`)}};function cn(f,w,E,S,q){f>>>=0,E>>>=0,w=Hr(w>>>0);let Q=ne=>ne;if(S=S===0n){let ne=8*E;Q=ye=>BigInt.asUintN(ne,ye),q=Q(q)}Zt(f,{name:w,Oc:Q,Vc:(ne,ye)=>(typeof ye=="number"&&(ye=BigInt(ye)),ye),Uc:Oa(w,E,!S),Wc:null})}function _s(f,w,E,S){Zt(f>>>=0,{name:w=Hr(w>>>0),Oc:function(q){return!!q},Vc:function(q,Q){return Q?E:S},Uc:function(q){return this.Oc((B(),Re)[q>>>0])},Wc:null})}var Er=[],rr=[0,1,,1,null,1,!0,1,!1,1];function wr(f){9<(f>>>=0)&&--rr[f+1]==0&&(rr[f]=void 0,Er.push(f))}var pr=f=>{if(!f)throw new pi(`Cannot use deleted val. handle = ${f}`);return rr[f]},fr=f=>{switch(f){case void 0:return 2;case null:return 4;case!0:return 6;case!1:return 8;default:let w=Er.pop()||rr.length;return rr[w]=f,rr[w+1]=1,w}};function xi(f){return this.Oc((B(),qe)[f>>>2>>>0])}var na={name:"emscripten::val",Oc:f=>{var w=pr(f);return wr(f),w},Vc:(f,w)=>fr(w),Uc:xi,Wc:null};function Ra(f){return Zt(f>>>0,na)}var it=(f,w)=>{switch(w){case 4:return function(E){return this.Oc((B(),Ve)[E>>>2>>>0])};case 8:return function(E){return this.Oc((B(),ze)[E>>>3>>>0])};default:throw new TypeError(`invalid float width (${w}): ${f}`)}};function sa(f,w,E){E>>>=0,Zt(f>>>=0,{name:w=Hr(w>>>0),Oc:S=>S,Vc:(S,q)=>q,Uc:it(w,E),Wc:null})}function hn(f,w,E,S,q){f>>>=0,E>>>=0,w=Hr(w>>>0);let Q=ye=>ye;if(S===0){var ne=32-8*E;Q=ye=>ye<<ne>>>ne,q=Q(q)}Zt(f,{name:w,Oc:Q,Vc:(ye,Oe)=>Oe,Uc:Oa(w,E,S!==0),Wc:null})}function fn(f,w,E){function S(Q){var ne=(B(),qe)[Q>>>2>>>0];return Q=(B(),qe)[Q+4>>>2>>>0],new q((B(),_e).buffer,Q,ne)}var q=[Int8Array,Uint8Array,Int16Array,Uint16Array,Int32Array,Uint32Array,Float32Array,Float64Array,BigInt64Array,BigUint64Array][w];Zt(f>>>=0,{name:E=Hr(E>>>0),Oc:S,Uc:S},{yd:!0})}var Br=(f,w,E)=>{var S=(B(),Re);if(w>>>=0,0<E){var q=w;E=w+E-1;for(var Q=0;Q<f.length;++Q){var ne=f.codePointAt(Q);if(127>=ne){if(w>=E)break;S[w++>>>0]=ne}else if(2047>=ne){if(w+1>=E)break;S[w++>>>0]=192|ne>>6,S[w++>>>0]=128|63&ne}else if(65535>=ne){if(w+2>=E)break;S[w++>>>0]=224|ne>>12,S[w++>>>0]=128|ne>>6&63,S[w++>>>0]=128|63&ne}else{if(w+3>=E)break;S[w++>>>0]=240|ne>>18,S[w++>>>0]=128|ne>>12&63,S[w++>>>0]=128|ne>>6&63,S[w++>>>0]=128|63&ne,Q++}}S[w>>>0]=0,f=w-q}else f=0;return f},Si=f=>{for(var w=0,E=0;E<f.length;++E){var S=f.charCodeAt(E);127>=S?w++:2047>=S?w+=2:55296<=S&&57343>=S?(w+=4,++E):w+=3}return w};function ci(f,w){Zt(f>>>=0,{name:w=Hr(w>>>0),Oc(E){var S=(B(),qe)[E>>>2>>>0];return S=Lt(E+4,S,!0),ar(E),S},Vc(E,S){S instanceof ArrayBuffer&&(S=new Uint8Array(S));var q=typeof S=="string";if(!(q||ArrayBuffer.isView(S)&&S.BYTES_PER_ELEMENT==1))throw new pi("Cannot pass non-string to std::string");var Q=q?Si(S):S.length,ne=ja(4+Q+1),ye=ne+4;return(B(),qe)[ne>>>2>>>0]=Q,q?Br(S,ye,Q+1):(B(),Re).set(S,ye>>>0),E!==null&&E.push(ar,ne),ne},Uc:xi,Wc(E){ar(E)}})}var oa=globalThis.TextDecoder?new TextDecoder("utf-16le"):void 0,Ba=(f,w,E)=>{if(f>>>=1,16<(w=Ia((B(),Me),f,w/2,E))-f&&oa)return oa.decode((B(),Me).slice(f,w));for(E="";f<w;++f){var S=(B(),Me)[f>>>0];E+=String.fromCharCode(S)}return E},mt=(f,w,E)=>{if(E??(E=2147483647),2>E)return 0;var S=w;E=(E-=2)<2*f.length?E/2:f.length;for(var q=0;q<E;++q){var Q=f.charCodeAt(q);(B(),Ue)[w>>>1>>>0]=Q,w+=2}return(B(),Ue)[w>>>1>>>0]=0,w-S},Mr=f=>2*f.length,Ma=(f,w,E)=>{var S="";f>>>=2;for(var q=0;!(q>=w/4);q++){var Q=(B(),qe)[f+q>>>0];if(!Q&&!E)break;S+=String.fromCodePoint(Q)}return S},hi=(f,w,E)=>{if(w>>>=0,E??(E=2147483647),4>E)return 0;var S=w;E=S+E-4;for(var q=0;q<f.length;++q){var Q=f.codePointAt(q);if(65535<Q&&q++,(B(),pe)[w>>>2>>>0]=Q,(w+=4)+4>E)break}return(B(),pe)[w>>>2>>>0]=0,w-S},he=f=>{for(var w=0,E=0;E<f.length;++E)65535<f.codePointAt(E)&&E++,w+=4;return w};function Fi(f,w,E){if(f>>>=0,w>>>=0,E=Hr(E>>>=0),w===2)var S=Ba,q=mt,Q=Mr;else S=Ma,q=hi,Q=he;Zt(f,{name:E,Oc:ne=>{var ye=(B(),qe)[ne>>>2>>>0];return ye=S(ne+4,ye*w,!0),ar(ne),ye},Vc:(ne,ye)=>{if(typeof ye!="string")throw new pi(`Cannot pass non-string to C++ string type ${E}`);var Oe=Q(ye),Le=ja(4+Oe+w);return(B(),qe)[Le>>>2>>>0]=Oe/w,q(ye,Le+4,Oe+w),ne!==null&&ne.push(ar,Le),Le},Uc:xi,Wc(ne){ar(ne)}})}function Da(f,w){Zt(f>>>=0,{zd:!0,name:w=Hr(w>>>0),Oc:()=>{},Vc:()=>{}})}function mn(f){ts(f>>>0,!s,1,!a,131072,!1),Zn()}var zr=f=>{if(!ee)try{if(f(),!(0<Ct))try{o?Ha()&&rs(A):tr(A)}catch(w){w instanceof jt||w=="unwind"||_(0,w)}}catch(w){w instanceof jt||w=="unwind"||_(0,w)}},Xe=!Atomics.waitAsync||((po=globalThis.navigator)==null?void 0:po.userAgent)&&91>Number((navigator.userAgent.match(/Chrom(e|ium)\/([0-9]+)\./)||[])[2]);function jr(f){f>>>=0,Xe||(Atomics.waitAsync((B(),pe),f>>>2,f).value.then(Jr),f+=128,Atomics.store((B(),pe),f>>>2,1))}var Jr=()=>zr(()=>{var f=Ha();f&&(jr(f),Ts())});function Ti(f,w){(f>>>=0)==w>>>0?setTimeout(Jr):o?postMessage({Zc:f,Sc:"checkMailbox"}):(f=Rr[f])&&f.postMessage({Sc:"checkMailbox"})}var fi=[];function ua(f,w,E,S,q){for(w>>>=0,q>>>=0,fi.length=0,E=q>>>3,S=q+S>>>3;E<S;){var Q;Q=(B(),ht)[E++>>>0]?(B(),ht)[E++>>>0]:(B(),ze)[E++>>>0],fi.push(Q)}return(w?ma[w]:Xo[f])(...fi)}var la=()=>{Ct=0};function ei(f){f>>>=0,o?postMessage({Sc:"cleanupThread",Nd:f}):Kn(Rr[f])}function mi(f){}var ki=f=>{try{f()}catch(w){Ie(w)}};function Na(f){var w=(...E)=>{da.push(f);try{return f(...E)}finally{ee||(da.pop(),Cr&&Dr===1&&da.length===0&&(Dr=0,Ct+=1,ki(ro),typeof Fibers<"u"&&Fibers.Zd()))}};return La.set(f,w),w}var Dr=0,Cr=null,Pa=0,da=[],gi=new Map,Ua=new Map,La=new Map,gn=0,Hi=null,qa=[],pa=f=>(function(w){if(!ee){if(Dr===0){var E=!1,S=!1;w((q=0)=>{if(!ee&&(Pa=q,E=!0,S)){Dr=2,ki(()=>io(Cr)),typeof MainLoop<"u"&&MainLoop.ud&&MainLoop.resume(),q=!1;try{var Q=(function(){var Oe=(B(),pe)[Cr+8>>>2>>>0];return Oe=Ua.get(Oe),Oe=La.get(Oe),--Ct,Oe()})()}catch(Oe){Q=Oe,q=!0}var ne=!1;if(!Cr){var ye=Hi;ye&&(Hi=null,(q?ye.reject:ye.resolve)(Q),ne=!0)}if(q&&!ne)throw Q}}),S=!0,E||(Dr=1,Cr=(function(){var q=ja(65548),Q=q+12;if((B(),qe)[q>>>2>>>0]=Q,(B(),qe)[q+4>>>2>>>0]=Q+65536,Q=da[0],!gi.has(Q)){var ne=gn++;gi.set(Q,ne),Ua.set(ne,Q)}return Q=gi.get(Q),(B(),pe)[q+8>>>2>>>0]=Q,q})(),typeof MainLoop<"u"&&MainLoop.ud&&MainLoop.pause(),ki(()=>to(Cr)))}else Dr===2?(Dr=0,ki(ao),ar(Cr),Cr=null,qa.forEach(zr)):Ie(`invalid state: ${Dr}`);return Pa}})(w=>{f().then(w)});function ca(f){return f>>>=0,pa(async()=>{var w=await pr(f);return fr(w)})}var ji=[],Va=f=>{var w=ji.length;return ji.push(f),w},Wa=(f,w)=>{for(var E=Array(f),S=0;S<f;++S){var q=S,Q=(B(),qe)[w+4*S>>>2>>>0],ne=aa[Q];if(ne===void 0)throw f=`parameter ${S}`,Q=bs(Q),w=Hr(Q),ar(Q),new pi(`${f} has unknown type ${w}`);E[q]=ne}return E},yn=(f,w,E)=>{var S=[];return f=f(S,E),S.length&&((B(),qe)[w>>>2>>>0]=fr(S)),f},_n={},ha=f=>{var w=_n[f];return w===void 0?Hr(f):w};function ws(f,w,E){var[S,...q]=Wa(f,w>>>0);w=S.Vc.bind(S);var Q=q.map(Oe=>Oe.Uc.bind(Oe));f--;var ne={toValue:pr};switch(f=Q.map((Oe,Le)=>{var pt=`argFromPtr${Le}`;return ne[pt]=Oe,`${pt}(args${Le?"+"+8*Le:""})`}),E){case 0:var ye="toValue(handle)";break;case 2:ye="new (toValue(handle))";break;case 3:ye="";break;case 1:ne.getStringOrSymbol=ha,ye="toValue(handle)[getStringOrSymbol(methodName)]"}return ye+=`(${f})`,S.zd||(ne.toReturnWire=w,ne.emval_returnValue=yn,ye=`return emval_returnValue(toReturnWire, destructorsRef, ${ye})`),ye=`return function (handle, methodName, destructorsRef, args) {
  ${ye}
  }`,E=new Function(Object.keys(ne),ye)(...Object.values(ne)),ye=`methodCaller<(${q.map(Oe=>Oe.name)}) => ${S.name}>`,Va(Object.defineProperty(E,"name",{value:ye}))}function z(f,w){return w>>>=0,(f=pr(f>>>0))==pr(w)}function N(f){return(f>>>=0)?(f=ha(f),fr(globalThis[f])):fr(globalThis)}function j(f){return f=ha(f>>>0),fr(r[f])}function te(f,w){return w>>>=0,f=pr(f>>>0),w=pr(w),fr(f[w])}function X(f){9<(f>>>=0)&&(rr[f+1]+=1)}function ue(f,w,E,S,q){return ji[f>>>0](w>>>0,E>>>0,S>>>0,q>>>0)}function re(f,w,E,S,q){return ue(f>>>0,w>>>0,E>>>0,S>>>0,q>>>0)}function ie(){return fr([])}function le(f){f=pr(f>>>0);for(var w=Array(f.length),E=0;E<f.length;E++)w[E]=f[E];return fr(w)}function ve(f){return fr(ha(f>>>0))}function Se(){return fr({})}function be(f){for(var w=pr(f>>>=0);w.length;){var E=w.pop();w.pop()(E)}wr(f)}function ce(f,w,E){w>>>=0,E>>>=0,f=pr(f>>>0),w=pr(w),E=pr(E),f[w]=E}function Fe(f,w){f=-9007199254740992>f||9007199254740992<f?NaN:Number(f),w>>>=0,f=new Date(1e3*f),(B(),pe)[w>>>2>>>0]=f.getUTCSeconds(),(B(),pe)[w+4>>>2>>>0]=f.getUTCMinutes(),(B(),pe)[w+8>>>2>>>0]=f.getUTCHours(),(B(),pe)[w+12>>>2>>>0]=f.getUTCDate(),(B(),pe)[w+16>>>2>>>0]=f.getUTCMonth(),(B(),pe)[w+20>>>2>>>0]=f.getUTCFullYear()-1900,(B(),pe)[w+24>>>2>>>0]=f.getUTCDay(),f=(f.getTime()-Date.UTC(f.getUTCFullYear(),0,1,0,0,0,0))/864e5|0,(B(),pe)[w+28>>>2>>>0]=f}var oe=f=>f%4==0&&(f%100!=0||f%400==0),ke=[0,31,60,91,121,152,182,213,244,274,305,335],qt=[0,31,59,90,120,151,181,212,243,273,304,334];function dt(f,w){f=-9007199254740992>f||9007199254740992<f?NaN:Number(f),w>>>=0,f=new Date(1e3*f),(B(),pe)[w>>>2>>>0]=f.getSeconds(),(B(),pe)[w+4>>>2>>>0]=f.getMinutes(),(B(),pe)[w+8>>>2>>>0]=f.getHours(),(B(),pe)[w+12>>>2>>>0]=f.getDate(),(B(),pe)[w+16>>>2>>>0]=f.getMonth(),(B(),pe)[w+20>>>2>>>0]=f.getFullYear()-1900,(B(),pe)[w+24>>>2>>>0]=f.getDay();var E=(oe(f.getFullYear())?ke:qt)[f.getMonth()]+f.getDate()-1|0;(B(),pe)[w+28>>>2>>>0]=E,(B(),pe)[w+36>>>2>>>0]=-60*f.getTimezoneOffset(),E=new Date(f.getFullYear(),6,1).getTimezoneOffset();var S=new Date(f.getFullYear(),0,1).getTimezoneOffset();f=0|(E!=S&&f.getTimezoneOffset()==Math.min(S,E)),(B(),pe)[w+32>>>2>>>0]=f}function lt(f){f>>>=0;var w=new Date((B(),pe)[f+20>>>2>>>0]+1900,(B(),pe)[f+16>>>2>>>0],(B(),pe)[f+12>>>2>>>0],(B(),pe)[f+8>>>2>>>0],(B(),pe)[f+4>>>2>>>0],(B(),pe)[f>>>2>>>0],0),E=(B(),pe)[f+32>>>2>>>0],S=w.getTimezoneOffset(),q=new Date(w.getFullYear(),6,1).getTimezoneOffset(),Q=new Date(w.getFullYear(),0,1).getTimezoneOffset(),ne=Math.min(Q,q);return 0>E?(B(),pe)[f+32>>>2>>>0]=+(q!=Q&&ne==S):0<E!=(ne==S)&&(q=Math.max(Q,q),w.setTime(w.getTime()+6e4*((0<E?ne:q)-S))),(B(),pe)[f+24>>>2>>>0]=w.getDay(),E=(oe(w.getFullYear())?ke:qt)[w.getMonth()]+w.getDate()-1|0,(B(),pe)[f+28>>>2>>>0]=E,(B(),pe)[f>>>2>>>0]=w.getSeconds(),(B(),pe)[f+4>>>2>>>0]=w.getMinutes(),(B(),pe)[f+8>>>2>>>0]=w.getHours(),(B(),pe)[f+12>>>2>>>0]=w.getDate(),(B(),pe)[f+16>>>2>>>0]=w.getMonth(),(B(),pe)[f+20>>>2>>>0]=w.getYear(),f=w.getTime(),BigInt(isNaN(f)?-1:f/1e3)}function Mt(f,w,E,S,q,Q,ne){return o?He(16,1,f,w,E,S,q,Q,ne):-52}function Ke(f,w,E,S,q,Q){if(o)return He(17,1,f,w,E,S,q,Q)}var Dt={},vt=()=>performance.timeOrigin+performance.now();function ir(f,w){if(o)return He(18,1,f,w);if(Dt[f]&&(clearTimeout(Dt[f].id),delete Dt[f]),!w)return 0;var E=setTimeout(()=>{delete Dt[f],zr(()=>Ss(f,performance.timeOrigin+performance.now()))},w);return Dt[f]={id:E,Yd:w},0}function mr(f,w,E,S){f>>>=0,w>>>=0,E>>>=0,S>>>=0;var q=new Date().getFullYear(),Q=new Date(q,0,1).getTimezoneOffset();q=new Date(q,6,1).getTimezoneOffset();var ne=Math.max(Q,q);(B(),qe)[f>>>2>>>0]=60*ne,(B(),pe)[w>>>2>>>0]=+(Q!=q),f=(w=ye=>{var Oe=Math.abs(ye);return`UTC${0<=ye?"-":"+"}${String(Math.floor(Oe/60)).padStart(2,"0")}${String(Oe%60).padStart(2,"0")}`})(Q),w=w(q),q<Q?(Br(f,E,17),Br(w,S,17)):(Br(f,S,17),Br(w,E,17))}var br=()=>Date.now();function fa(f,w,E){return E>>>=0,0<=f&&3>=f?(f===0?f=Date.now():f=performance.timeOrigin+performance.now(),f=Math.round(1e6*f),(B(),ht)[E>>>3>>>0]=BigInt(f),0):28}var cr=[],Ga=(f,w)=>{cr.length=0;for(var E;E=(B(),Re)[f++>>>0];){var S=E!=105;w+=(S&=E!=112)&&w%8?4:0,cr.push(E==112?(B(),qe)[w>>>2>>>0]:E==106?(B(),ht)[w>>>3>>>0]:E==105?(B(),pe)[w>>>2>>>0]:(B(),ze)[w>>>3>>>0]),w+=S?8:4}return cr};function wn(f,w,E){return f>>>=0,w=Ga(w>>>0,E>>>0),ma[f](...w)}function or(f,w,E){return f>>>=0,w=Ga(w>>>0,E>>>0),ma[f](...w)}var Kr=()=>{};function bn(f,w){return K(Lt(f>>>0,w>>>0))}var wt=()=>{throw Ct+=1,"unwind"};function Nr(){return 4294901760}var Jn=()=>navigator.hardwareConcurrency,gr={},yi=f=>{var w;return(w=/\bwasm-function\[\d+\]:(0x[0-9a-f]+)/.exec(f))?+w[1]:(w=/:(\d+):\d+(?:\)|$)/.exec(f))?2147483648|+w[1]:0},Qt=f=>{for(var w of f)(f=yi(w))&&(gr[f]=w)};function $r(){var f=Error().stack.toString().split(`
`);return f[0]=="Error"&&f.shift(),Qt(f),gr.gd=yi(f[3]),gr.Jd=f,gr.gd}function Ei(f){if(!(f=gr[f>>>0]))return 0;var w;if(w=/^\s+at .*\.wasm\.(.*) \(.*\)$/.exec(f))f=w[1];else if(w=/^\s+at (.*) \(.*\)$/.exec(f))f=w[1];else{if(!(w=/^(.+?)@/.exec(f)))return 0;f=w[1]}ar(Ei.hd??0),w=Si(f)+1;var E=ja(w);return E&&Br(f,E,w),Ei.hd=E,Ei.hd}function vr(f){f>>>=0;var w=(B(),Re).length;if(f<=w||4294901760<f)return!1;for(var E=1;4>=E;E*=2){var S=w*(1+.2/E);S=Math.min(S,f+100663296);e:{S=(Math.min(4294901760,65536*Math.ceil(Math.max(f,S)/65536))-oi.buffer.byteLength+65535)/65536|0;try{oi.grow(S),Be();var q=1;break e}catch{}q=void 0}if(q)return!0}return!1}function $n(f,w,E){if(f>>>=0,w>>>=0,gr.gd==f)var S=gr.Jd;else(S=Error().stack.toString().split(`
`))[0]=="Error"&&S.shift(),Qt(S);for(var q=3;S[q]&&yi(S[q])!=f;)++q;for(f=0;f<E&&S[f+q];++f)(B(),pe)[w+4*f>>>2>>>0]=yi(S[f+q]);return f}var Fa,Pr={},Zr=()=>{var S;if(!Fa){var f,w={USER:"web_user",LOGNAME:"web_user",PATH:"/",PWD:"/",HOME:"/home/web_user",LANG:(((S=globalThis.navigator)==null?void 0:S.language)??"C").replace("-","_")+".UTF-8",_:"./this.program"};for(f in Pr)Pr[f]===void 0?delete w[f]:w[f]=Pr[f];var E=[];for(f in w)E.push(`${f}=${w[f]}`);Fa=E}return Fa};function Ii(f,w){if(o)return He(19,1,f,w);f>>>=0,w>>>=0;var E,S=0,q=0;for(E of Zr()){var Q=w+S;(B(),qe)[f+q>>>2>>>0]=Q,S+=Br(E,Q,1/0)+1,q+=4}return 0}function Xt(f,w){if(o)return He(20,1,f,w);f>>>=0,w>>>=0;var E=Zr();for(var S of((B(),qe)[f>>>2>>>0]=E.length,f=0,E))f+=Si(S)+1;return(B(),qe)[w>>>2>>>0]=f,0}function Gt(f){return o?He(21,1,f):52}function Ur(f,w,E,S){return o?He(22,1,f,w,E,S):52}function vn(f,w,E,S){return o?He(23,1,f,w,E,S):70}var es=[null,[],[]];function jo(f,w,E,S){if(o)return He(24,1,f,w,E,S);w>>>=0,E>>>=0,S>>>=0;for(var q=0,Q=0;Q<E;Q++){var ne=(B(),qe)[w>>>2>>>0],ye=(B(),qe)[w+4>>>2>>>0];w+=8;for(var Oe=0;Oe<ye;Oe++){var Le=f,pt=(B(),Re)[ne+Oe>>>0],kt=es[Le];pt===0||pt===10?((Le===1?G:K)(qi(kt)),kt.length=0):kt.push(pt)}q+=ye}return(B(),qe)[S>>>2>>>0]=q,0}function ti(f){return f>>>0}o||(function(){for(var f=r.numThreads-1;f--;)Xn();er.push(async()=>{var w=(async function(){if(!o)return Promise.all(Wr.map(Qn))})();St++,await w,--St==0&&dr&&(w=dr,dr=null,w())})})(),o||(oi=new WebAssembly.Memory({initial:256,maximum:65536,shared:!0}),Be()),r.wasmBinary&&(T=r.wasmBinary),r.stackSave=()=>yt(),r.stackRestore=f=>gt(f),r.stackAlloc=f=>is(f),r.setValue=function(f,w,E="i8"){switch(E.endsWith("*")&&(E="*"),E){case"i1":case"i8":(B(),_e)[f>>>0]=w;break;case"i16":(B(),Ue)[f>>>1>>>0]=w;break;case"i32":(B(),pe)[f>>>2>>>0]=w;break;case"i64":(B(),ht)[f>>>3>>>0]=BigInt(w);break;case"float":(B(),Ve)[f>>>2>>>0]=w;break;case"double":(B(),ze)[f>>>3>>>0]=w;break;case"*":(B(),qe)[f>>>2>>>0]=w;break;default:Ie(`invalid type for setValue: ${E}`)}},r.getValue=function(f,w="i8"){switch(w.endsWith("*")&&(w="*"),w){case"i1":case"i8":return(B(),_e)[f>>>0];case"i16":return(B(),Ue)[f>>>1>>>0];case"i32":return(B(),pe)[f>>>2>>>0];case"i64":return(B(),ht)[f>>>3>>>0];case"float":return(B(),Ve)[f>>>2>>>0];case"double":return(B(),ze)[f>>>3>>>0];case"*":return(B(),qe)[f>>>2>>>0];default:Ie(`invalid type for getValue: ${w}`)}},r.UTF8ToString=Lt,r.stringToUTF8=Br,r.lengthBytesUTF8=Si;var bs,xn,Ha,ar,ja,ts,$s,vs,xs,rs,Ss,Ts,bt,Ka,Qr,gt,is,yt,ks,as,Es,Is,zs,ns,Cs,ss,os,As,Os,us,Ko,Rs,Sn,Bs,Ms,Ds,Za,Ns,Ps,ls,Us,Ls,qs,Vs,Zo,Ws,Gs,Fs,Qo,Hs,Tt,js,Ks,Zs,Qs,Xs,Ys,Js,eo,to,ro,io,ao,_i,Xo=[Rt,sr,Ea,li,Fr,Vi,za,Ca,Aa,Wi,st,kr,Ht,di,Ye,Gi,Mt,Ke,ir,Ii,Xt,Gt,Ur,vn,jo],ma={973212:(f,w,E,S,q)=>{if(r===void 0||!r.Xc)return 1;if((f=Lt(Number(f>>>0))).startsWith("./")&&(f=f.substring(2)),!(f=r.Xc.get(f)))return 2;if(w=Number(w>>>0),E=Number(E>>>0),S=Number(S>>>0),w+E>f.byteLength)return 3;try{let Q=f.subarray(w,w+E);switch(q){case 0:(B(),Re).set(Q,S>>>0);break;case 1:r.Qd?r.Qd(S,Q):r.Id(S,Q);break;default:return 4}return 0}catch{return 4}},974036:(f,w,E)=>{r.td(f,(B(),Re).subarray(w>>>0,w+E>>>0))},974100:()=>r.Wd(),974142:f=>{r.sd(f)},974179:()=>{r.Bd()},974210:()=>{r.Cd()},974239:()=>{r.Gd()},974264:f=>r.Ad(f),974297:f=>r.Ed(f),974329:(f,w,E)=>{r.ed(Number(f),Number(w),Number(E),!0)},974392:(f,w,E)=>{r.ed(Number(f),Number(w),Number(E))},974449:()=>typeof wasmOffsetConverter<"u",974506:f=>{r.$b("Abs",f,void 0)},974557:f=>{r.$b("Neg",f,void 0)},974608:f=>{r.$b("Floor",f,void 0)},974661:f=>{r.$b("Ceil",f,void 0)},974713:f=>{r.$b("Reciprocal",f,void 0)},974771:f=>{r.$b("Sqrt",f,void 0)},974823:f=>{r.$b("Exp",f,void 0)},974874:f=>{r.$b("Erf",f,void 0)},974925:f=>{r.$b("Sigmoid",f,void 0)},974980:(f,w,E)=>{r.$b("HardSigmoid",f,{alpha:w,beta:E})},975059:f=>{r.$b("Log",f,void 0)},975110:f=>{r.$b("Sin",f,void 0)},975161:f=>{r.$b("Cos",f,void 0)},975212:f=>{r.$b("Tan",f,void 0)},975263:f=>{r.$b("Asin",f,void 0)},975315:f=>{r.$b("Acos",f,void 0)},975367:f=>{r.$b("Atan",f,void 0)},975419:f=>{r.$b("Sinh",f,void 0)},975471:f=>{r.$b("Cosh",f,void 0)},975523:f=>{r.$b("Asinh",f,void 0)},975576:f=>{r.$b("Acosh",f,void 0)},975629:f=>{r.$b("Atanh",f,void 0)},975682:f=>{r.$b("Tanh",f,void 0)},975734:f=>{r.$b("Not",f,void 0)},975785:(f,w,E)=>{r.$b("Clip",f,{min:w,max:E})},975854:f=>{r.$b("Clip",f,void 0)},975906:(f,w)=>{r.$b("Elu",f,{alpha:w})},975964:f=>{r.$b("Gelu",f,void 0)},976016:f=>{r.$b("Relu",f,void 0)},976068:(f,w)=>{r.$b("LeakyRelu",f,{alpha:w})},976132:(f,w)=>{r.$b("ThresholdedRelu",f,{alpha:w})},976202:(f,w)=>{r.$b("Cast",f,{to:w})},976260:f=>{r.$b("Add",f,void 0)},976311:f=>{r.$b("Sub",f,void 0)},976362:f=>{r.$b("Mul",f,void 0)},976413:f=>{r.$b("Div",f,void 0)},976464:f=>{r.$b("Pow",f,void 0)},976515:f=>{r.$b("Equal",f,void 0)},976568:f=>{r.$b("Greater",f,void 0)},976623:f=>{r.$b("GreaterOrEqual",f,void 0)},976685:f=>{r.$b("Less",f,void 0)},976737:f=>{r.$b("LessOrEqual",f,void 0)},976796:(f,w,E,S,q)=>{r.$b("ReduceMean",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},976971:(f,w,E,S,q)=>{r.$b("ReduceMax",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},977145:(f,w,E,S,q)=>{r.$b("ReduceMin",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},977319:(f,w,E,S,q)=>{r.$b("ReduceProd",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},977494:(f,w,E,S,q)=>{r.$b("ReduceSum",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},977668:(f,w,E,S,q)=>{r.$b("ReduceL1",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},977841:(f,w,E,S,q)=>{r.$b("ReduceL2",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},978014:(f,w,E,S,q)=>{r.$b("ReduceLogSum",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},978191:(f,w,E,S,q)=>{r.$b("ReduceSumSquare",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},978371:(f,w,E,S,q)=>{r.$b("ReduceLogSumExp",f,{keepDims:!!w,noopWithEmptyAxes:!!E,axes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},978551:f=>{r.$b("Where",f,void 0)},978604:(f,w,E)=>{r.$b("Transpose",f,{perm:w?Array.from((B(),pe).subarray(Number(w)>>>0,Number(E)>>>0)):[]})},978728:(f,w,E,S)=>{r.$b("DepthToSpace",f,{blocksize:w,mode:Lt(E),format:S?"NHWC":"NCHW"})},978861:(f,w,E,S)=>{r.$b("DepthToSpace",f,{blocksize:w,mode:Lt(E),format:S?"NHWC":"NCHW"})},978994:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At,Ar)=>{r.$b("ConvTranspose",f,{format:Oe?"NHWC":"NCHW",autoPad:w,dilations:[E],group:S,kernelShape:[q],pads:[Q,ne],strides:[ye],wIsConst:()=>!!(B(),_e)[Le>>>0],outputPadding:pt?Array.from((B(),pe).subarray(Number(pt)>>>0,Number(kt)>>>0)):[],outputShape:Et?Array.from((B(),pe).subarray(Number(Et)>>>0,Number(At)>>>0)):[],activation:Lt(Ar)})},979427:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At)=>{r.$b("ConvTranspose",f,{format:ye?"NHWC":"NCHW",autoPad:w,dilations:Array.from((B(),pe).subarray(Number(E)>>>0,2+(Number(E)>>>0)>>>0)),group:S,kernelShape:Array.from((B(),pe).subarray(Number(q)>>>0,2+(Number(q)>>>0)>>>0)),pads:Array.from((B(),pe).subarray(Number(Q)>>>0,4+(Number(Q)>>>0)>>>0)),strides:Array.from((B(),pe).subarray(Number(ne)>>>0,2+(Number(ne)>>>0)>>>0)),wIsConst:()=>!!(B(),_e)[Oe>>>0],outputPadding:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],outputShape:kt?Array.from((B(),pe).subarray(Number(kt)>>>0,Number(Et)>>>0)):[],activation:Lt(At)})},980088:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At,Ar)=>{r.$b("ConvTranspose",f,{format:Oe?"NHWC":"NCHW",autoPad:w,dilations:[E],group:S,kernelShape:[q],pads:[Q,ne],strides:[ye],wIsConst:()=>!!(B(),_e)[Le>>>0],outputPadding:pt?Array.from((B(),pe).subarray(Number(pt)>>>0,Number(kt)>>>0)):[],outputShape:Et?Array.from((B(),pe).subarray(Number(Et)>>>0,Number(At)>>>0)):[],activation:Lt(Ar)})},980521:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At)=>{r.$b("ConvTranspose",f,{format:ye?"NHWC":"NCHW",autoPad:w,dilations:Array.from((B(),pe).subarray(Number(E)>>>0,2+(Number(E)>>>0)>>>0)),group:S,kernelShape:Array.from((B(),pe).subarray(Number(q)>>>0,2+(Number(q)>>>0)>>>0)),pads:Array.from((B(),pe).subarray(Number(Q)>>>0,4+(Number(Q)>>>0)>>>0)),strides:Array.from((B(),pe).subarray(Number(ne)>>>0,2+(Number(ne)>>>0)>>>0)),wIsConst:()=>!!(B(),_e)[Oe>>>0],outputPadding:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],outputShape:kt?Array.from((B(),pe).subarray(Number(kt)>>>0,Number(Et)>>>0)):[],activation:Lt(At)})},981182:(f,w)=>{r.$b("GlobalAveragePool",f,{format:w?"NHWC":"NCHW"})},981273:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At)=>{r.$b("AveragePool",f,{format:At?"NHWC":"NCHW",auto_pad:w,ceil_mode:E,count_include_pad:S,storage_order:q,dilations:Q?Array.from((B(),pe).subarray(Number(Q)>>>0,Number(ne)>>>0)):[],kernel_shape:ye?Array.from((B(),pe).subarray(Number(ye)>>>0,Number(Oe)>>>0)):[],pads:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],strides:kt?Array.from((B(),pe).subarray(Number(kt)>>>0,Number(Et)>>>0)):[]})},981752:(f,w)=>{r.$b("GlobalAveragePool",f,{format:w?"NHWC":"NCHW"})},981843:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At)=>{r.$b("AveragePool",f,{format:At?"NHWC":"NCHW",auto_pad:w,ceil_mode:E,count_include_pad:S,storage_order:q,dilations:Q?Array.from((B(),pe).subarray(Number(Q)>>>0,Number(ne)>>>0)):[],kernel_shape:ye?Array.from((B(),pe).subarray(Number(ye)>>>0,Number(Oe)>>>0)):[],pads:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],strides:kt?Array.from((B(),pe).subarray(Number(kt)>>>0,Number(Et)>>>0)):[]})},982322:(f,w)=>{r.$b("GlobalMaxPool",f,{format:w?"NHWC":"NCHW"})},982409:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At)=>{r.$b("MaxPool",f,{format:At?"NHWC":"NCHW",auto_pad:w,ceil_mode:E,count_include_pad:S,storage_order:q,dilations:Q?Array.from((B(),pe).subarray(Number(Q)>>>0,Number(ne)>>>0)):[],kernel_shape:ye?Array.from((B(),pe).subarray(Number(ye)>>>0,Number(Oe)>>>0)):[],pads:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],strides:kt?Array.from((B(),pe).subarray(Number(kt)>>>0,Number(Et)>>>0)):[]})},982884:(f,w)=>{r.$b("GlobalMaxPool",f,{format:w?"NHWC":"NCHW"})},982971:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At)=>{r.$b("MaxPool",f,{format:At?"NHWC":"NCHW",auto_pad:w,ceil_mode:E,count_include_pad:S,storage_order:q,dilations:Q?Array.from((B(),pe).subarray(Number(Q)>>>0,Number(ne)>>>0)):[],kernel_shape:ye?Array.from((B(),pe).subarray(Number(ye)>>>0,Number(Oe)>>>0)):[],pads:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],strides:kt?Array.from((B(),pe).subarray(Number(kt)>>>0,Number(Et)>>>0)):[]})},983446:(f,w,E,S,q)=>{r.$b("Gemm",f,{alpha:w,beta:E,transA:S,transB:q})},983550:f=>{r.$b("MatMul",f,void 0)},983604:(f,w,E,S)=>{r.$b("ArgMax",f,{keepDims:!!w,selectLastIndex:!!E,axis:S})},983712:(f,w,E,S)=>{r.$b("ArgMin",f,{keepDims:!!w,selectLastIndex:!!E,axis:S})},983820:(f,w)=>{r.$b("Softmax",f,{axis:w})},983883:(f,w)=>{r.$b("Concat",f,{axis:w})},983943:(f,w,E,S,q)=>{r.$b("Split",f,{axis:w,numOutputs:E,splitSizes:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},984099:f=>{r.$b("Expand",f,void 0)},984153:(f,w)=>{r.$b("Gather",f,{axis:Number(w)})},984224:(f,w)=>{r.$b("GatherElements",f,{axis:Number(w)})},984303:(f,w)=>{r.$b("GatherND",f,{batch_dims:Number(w)})},984382:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt)=>{r.$b("Resize",f,{antialias:w,axes:E?Array.from((B(),pe).subarray(Number(E)>>>0,Number(S)>>>0)):[],coordinateTransformMode:Lt(q),cubicCoeffA:Q,excludeOutside:ne,extrapolationValue:ye,keepAspectRatioPolicy:Lt(Oe),mode:Lt(Le),nearestMode:Lt(pt)})},984744:(f,w,E,S,q,Q,ne)=>{r.$b("Slice",f,{starts:w?Array.from((B(),pe).subarray(Number(w)>>>0,Number(E)>>>0)):[],ends:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[],axes:Q?Array.from((B(),pe).subarray(Number(Q)>>>0,Number(ne)>>>0)):[]})},985008:f=>{r.$b("Tile",f,void 0)},985060:(f,w,E)=>{r.$b("InstanceNormalization",f,{epsilon:w,format:E?"NHWC":"NCHW"})},985174:(f,w,E)=>{r.$b("InstanceNormalization",f,{epsilon:w,format:E?"NHWC":"NCHW"})},985288:f=>{r.$b("Range",f,void 0)},985341:(f,w)=>{r.$b("Einsum",f,{equation:Lt(w)})},985422:(f,w,E,S,q)=>{r.$b("Pad",f,{mode:w,value:E,pads:S?Array.from((B(),pe).subarray(Number(S)>>>0,Number(q)>>>0)):[]})},985565:(f,w,E,S,q,Q)=>{r.$b("BatchNormalization",f,{epsilon:w,momentum:E,spatial:!!q,trainingMode:!!S,format:Q?"NHWC":"NCHW"})},985734:(f,w,E,S,q,Q)=>{r.$b("BatchNormalization",f,{epsilon:w,momentum:E,spatial:!!q,trainingMode:!!S,format:Q?"NHWC":"NCHW"})},985903:(f,w,E)=>{r.$b("CumSum",f,{exclusive:Number(w),reverse:Number(E)})},986e3:(f,w,E)=>{r.$b("DequantizeLinear",f,{axis:w,blockSize:E})},986090:(f,w,E,S,q)=>{r.$b("GridSample",f,{align_corners:w,mode:Lt(E),padding_mode:Lt(S),format:q?"NHWC":"NCHW"})},986260:(f,w,E,S,q)=>{r.$b("GridSample",f,{align_corners:w,mode:Lt(E),padding_mode:Lt(S),format:q?"NHWC":"NCHW"})},986430:(f,w)=>{r.$b("ScatterND",f,{reduction:Lt(w)})},986515:(f,w,E,S,q,Q,ne,ye,Oe)=>{r.$b("Attention",f,{numHeads:w,isUnidirectional:E,maskFilterValue:S,scale:q,doRotary:Q,qkvHiddenSizes:ne?Array.from((B(),pe).subarray(Number(ye)>>>0,Number(ye)+ne>>>0)):[],pastPresentShareBuffer:!!Oe})},986787:f=>{r.$b("BiasAdd",f,void 0)},986842:f=>{r.$b("BiasSplitGelu",f,void 0)},986903:f=>{r.$b("FastGelu",f,void 0)},986959:(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At,Ar,kn)=>{r.$b("Conv",f,{format:kt?"NHWC":"NCHW",auto_pad:w,dilations:E?Array.from((B(),pe).subarray(Number(E)>>>0,Number(S)>>>0)):[],group:q,kernel_shape:Q?Array.from((B(),pe).subarray(Number(Q)>>>0,Number(ne)>>>0)):[],pads:ye?Array.from((B(),pe).subarray(Number(ye)>>>0,Number(Oe)>>>0)):[],strides:Le?Array.from((B(),pe).subarray(Number(Le)>>>0,Number(pt)>>>0)):[],w_is_const:()=>!!(B(),_e)[Number(Et)>>>0],activation:Lt(At),activation_params:Ar?Array.from((B(),Ve).subarray(Number(Ar)>>>0,Number(kn)>>>0)):[]})},987543:f=>{r.$b("Gelu",f,void 0)},987595:(f,w,E,S,q,Q,ne,ye,Oe)=>{r.$b("GroupQueryAttention",f,{numHeads:w,kvNumHeads:E,scale:S,softcap:q,doRotary:Q,rotaryInterleaved:ne,smoothSoftmax:ye,localWindowSize:Oe})},987812:(f,w,E,S)=>{r.$b("LayerNormalization",f,{axis:w,epsilon:E,simplified:!!S})},987923:(f,w,E,S)=>{r.$b("LayerNormalization",f,{axis:w,epsilon:E,simplified:!!S})},988034:(f,w,E,S,q,Q)=>{r.$b("MatMulNBits",f,{k:w,n:E,accuracyLevel:S,bits:q,blockSize:Q})},988161:(f,w,E,S,q,Q)=>{r.$b("MultiHeadAttention",f,{numHeads:w,isUnidirectional:E,maskFilterValue:S,scale:q,doRotary:Q})},988320:(f,w)=>{r.$b("QuickGelu",f,{alpha:w})},988384:(f,w,E,S,q)=>{r.$b("RotaryEmbedding",f,{interleaved:!!w,numHeads:E,rotaryEmbeddingDim:S,scale:q})},988523:(f,w,E)=>{r.$b("SkipLayerNormalization",f,{epsilon:w,simplified:!!E})},988625:(f,w,E)=>{r.$b("SkipLayerNormalization",f,{epsilon:w,simplified:!!E})},988727:(f,w,E,S)=>{r.$b("GatherBlockQuantized",f,{gatherAxis:w,quantizeAxis:E,blockSize:S})},988848:f=>{r.Fd(f)},988882:(f,w)=>r.Hd(Number(f),Number(w),r.Yc.Kd,r.Yc.errors)};function Yo(f,w,E){return pa(async()=>{await r.Dd(Number(f),Number(w),Number(E))})}function ds(){return typeof wasmOffsetConverter<"u"}function Jo(f,w,E,S){var q=yt();try{return Rs(f,w,E,S)}catch(Q){if(gt(q),Q!==Q+0)throw Q;bt(1,0)}}function eu(f,w,E){var S=yt();try{return As(f,w,E)}catch(q){if(gt(S),q!==q+0)throw q;bt(1,0)}}function tu(f){var w=yt();try{Cs(f)}catch(E){if(gt(w),E!==E+0)throw E;bt(1,0)}}function ru(f,w){var E=yt();try{return ns(f,w)}catch(S){if(gt(E),S!==S+0)throw S;bt(1,0)}}function iu(f,w,E){var S=yt();try{zs(f,w,E)}catch(q){if(gt(S),q!==q+0)throw q;bt(1,0)}}function au(f,w){var E=yt();try{Sn(f,w)}catch(S){if(gt(E),S!==S+0)throw S;bt(1,0)}}function nu(f,w,E,S,q,Q,ne){var ye=yt();try{return us(f,w,E,S,q,Q,ne)}catch(Oe){if(gt(ye),Oe!==Oe+0)throw Oe;bt(1,0)}}function su(f,w,E,S,q,Q){var ne=yt();try{ss(f,w,E,S,q,Q)}catch(ye){if(gt(ne),ye!==ye+0)throw ye;bt(1,0)}}function ou(f,w,E,S){var q=yt();try{Ko(f,w,E,S)}catch(Q){if(gt(q),Q!==Q+0)throw Q;bt(1,0)}}function uu(f,w,E,S,q){var Q=yt();try{os(f,w,E,S,q)}catch(ne){if(gt(Q),ne!==ne+0)throw ne;bt(1,0)}}function lu(f,w,E,S,q,Q,ne){var ye=yt();try{Ms(f,w,E,S,q,Q,ne)}catch(Oe){if(gt(ye),Oe!==Oe+0)throw Oe;bt(1,0)}}function du(f,w,E,S,q,Q,ne){var ye=yt();try{Ds(f,w,E,S,q,Q,ne)}catch(Oe){if(gt(ye),Oe!==Oe+0)throw Oe;bt(1,0)}}function pu(f,w,E,S,q,Q,ne,ye){var Oe=yt();try{ls(f,w,E,S,q,Q,ne,ye)}catch(Le){if(gt(Oe),Le!==Le+0)throw Le;bt(1,0)}}function cu(f,w,E,S,q){var Q=yt();try{return Bs(f,w,E,S,q)}catch(ne){if(gt(Q),ne!==ne+0)throw ne;bt(1,0)}}function hu(f,w,E){var S=yt();try{return Us(f,w,E)}catch(q){if(gt(S),q!==q+0)throw q;bt(1,0)}}function fu(f,w,E,S,q,Q,ne,ye){var Oe=yt();try{Ls(f,w,E,S,q,Q,ne,ye)}catch(Le){if(gt(Oe),Le!==Le+0)throw Le;bt(1,0)}}function no(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt){var Et=yt();try{Za(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt)}catch(At){if(gt(Et),At!==At+0)throw At;bt(1,0)}}function mu(f,w,E,S,q,Q){var ne=yt();try{return Ns(f,w,E,S,q,Q)}catch(ye){if(gt(ne),ye!==ye+0)throw ye;bt(1,0)}}function so(f,w,E){var S=yt();try{return qs(f,w,E)}catch(q){if(gt(S),q!==q+0)throw q;return bt(1,0),0n}}function oo(f,w,E,S,q,Q,ne,ye,Oe){var Le=yt();try{Os(f,w,E,S,q,Q,ne,ye,Oe)}catch(pt){if(gt(Le),pt!==pt+0)throw pt;bt(1,0)}}function gu(f){var w=yt();try{return Vs(f)}catch(E){if(gt(w),E!==E+0)throw E;bt(1,0)}}function yu(f,w){var E=yt();try{return eo(f,w)}catch(S){if(gt(E),S!==S+0)throw S;return bt(1,0),0n}}function _u(f){var w=yt();try{return Zo(f)}catch(E){if(gt(w),E!==E+0)throw E;return bt(1,0),0n}}function wu(f,w,E,S){var q=yt();try{return Tt(f,w,E,S)}catch(Q){if(gt(q),Q!==Q+0)throw Q;bt(1,0)}}function bu(f,w,E,S,q){var Q=yt();try{return js(f,w,E,S,q)}catch(ne){if(gt(Q),ne!==ne+0)throw ne;bt(1,0)}}function $u(f,w,E,S,q,Q){var ne=yt();try{return Ks(f,w,E,S,q,Q)}catch(ye){if(gt(ne),ye!==ye+0)throw ye;bt(1,0)}}function uo(f,w,E,S,q,Q){var ne=yt();try{return Zs(f,w,E,S,q,Q)}catch(ye){if(gt(ne),ye!==ye+0)throw ye;bt(1,0)}}function vu(f,w,E,S,q,Q,ne,ye){var Oe=yt();try{return Ps(f,w,E,S,q,Q,ne,ye)}catch(Le){if(gt(Oe),Le!==Le+0)throw Le;bt(1,0)}}function xu(f,w,E,S,q){var Q=yt();try{return Qs(f,w,E,S,q)}catch(ne){if(gt(Q),ne!==ne+0)throw ne;return bt(1,0),0n}}function Su(f,w,E,S){var q=yt();try{return Xs(f,w,E,S)}catch(Q){if(gt(q),Q!==Q+0)throw Q;bt(1,0)}}function pl(f,w,E,S){var q=yt();try{return Ys(f,w,E,S)}catch(Q){if(gt(q),Q!==Q+0)throw Q;bt(1,0)}}function Tu(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt){var Et=yt();try{return Js(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt)}catch(At){if(gt(Et),At!==At+0)throw At;bt(1,0)}}function ku(f,w,E,S,q,Q,ne,ye,Oe,Le,pt){var kt=yt();try{Qo(f,w,E,S,q,Q,ne,ye,Oe,Le,pt)}catch(Et){if(gt(kt),Et!==Et+0)throw Et;bt(1,0)}}function ri(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At,Ar,kn){var ga=yt();try{Hs(f,w,E,S,q,Q,ne,ye,Oe,Le,pt,kt,Et,At,Ar,kn)}catch(nr){if(gt(ga),nr!==nr+0)throw nr;bt(1,0)}}function Eu(f,w,E){var S=yt();try{return Ws(f,w,E)}catch(q){if(gt(S),q!==q+0)throw q;bt(1,0)}}function Iu(f,w,E){var S=yt();try{return Gs(f,w,E)}catch(q){if(gt(S),q!==q+0)throw q;bt(1,0)}}function zu(f,w,E,S){var q=yt();try{Fs(f,w,E,S)}catch(Q){if(gt(q),Q!==Q+0)throw Q;bt(1,0)}}function Tn(){if(0<St)dr=Tn;else if(o)R==null||R(r),We();else{for(var f=er;0<f.length;)f.shift()(r);0<St?dr=Tn:(r.calledRun=!0,ee||(We(),R==null||R(r)))}}return o||(_i=await _r(),Tn()),r.PTR_SIZE=4,Te?r:new Promise((f,w)=>{R=f,H=w})}var p0,ng,K2=Ee(()=>{var e,r;p0=ag,ng=(r=(e=globalThis.self)==null?void 0:e.name)==null?void 0:r.startsWith("em-pthread"),ng&&ag()}),hd,ap,sg,ai,c0,Lu,og,ug,fd,lg,md,h0,gd,f0,Tp=Ee(()=>{Sp(),hd=typeof location>"u"?void 0:location.origin,ap=import.meta.url>"file:"&&import.meta.url<"file;",sg=()=>{{if(ap){let e=URL;return new URL(new e("ort.bundle.min.mjs",import.meta.url).href,hd).href}return import.meta.url}},ai=sg(),c0=()=>{if(ai&&!ai.startsWith("blob:"))return ai.substring(0,ai.lastIndexOf("/")+1)},Lu=(e,r)=>{try{let a=r??ai;return(a?new URL(e,a):new URL(e)).origin===hd}catch{return!1}},og=(e,r)=>{let a=r??ai;try{return(a?new URL(e,a):new URL(e)).href}catch{return}},ug=(e,r)=>`${r??"./"}${e}`,fd=async e=>{let r=await(await fetch(e,{credentials:"same-origin"})).blob();return URL.createObjectURL(r)},lg=async e=>(await import(e)).default,md=(j2(),Go(u0)).default,h0=async()=>{if(!ai)throw new Error("Failed to load proxy worker: cannot determine the script source URL.");if(Lu(ai))return[void 0,md()];let e=await fd(ai);return[e,md(e)]},gd=(K2(),Go(d0)).default,f0=async(e,r,a,s)=>{let o=gd&&!(e||r);if(o)if(ai)o=Lu(ai)||s&&!a;else if(s&&!a)o=!0;else throw new Error("cannot determine the script source URL.");if(o)return[void 0,gd];{let p="ort-wasm-simd-threaded.jsep.mjs",d=e??og(p,r),g=a&&d&&!Lu(d,r),m=g?await fd(d):d??ug(p,r);return[g?m:void 0,await lg(m)]}}}),yd,qu,zo,_d,dg,pg,cg,kp,Wt,Fn=Ee(()=>{Tp(),qu=!1,zo=!1,_d=!1,dg=()=>{if(typeof SharedArrayBuffer>"u")return!1;try{return typeof MessageChannel<"u"&&new MessageChannel().port1.postMessage(new SharedArrayBuffer(1)),WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,5,4,1,3,1,1,10,11,1,9,0,65,0,254,16,2,0,26,11]))}catch{return!1}},pg=()=>{try{return WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,10,30,1,28,0,65,0,253,15,253,12,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,253,186,1,26,11]))}catch{return!1}},cg=()=>{try{return WebAssembly.validate(new Uint8Array([0,97,115,109,1,0,0,0,1,5,1,96,0,1,123,3,2,1,0,10,19,1,17,0,65,1,253,15,65,2,253,15,65,3,253,15,253,147,2,11]))}catch{return!1}},kp=async e=>{if(qu)return Promise.resolve();if(zo)throw new Error("multiple calls to 'initializeWebAssembly()' detected.");if(_d)throw new Error("previous call to 'initializeWebAssembly()' failed.");zo=!0;let r=e.initTimeout,a=e.numThreads;if(e.simd!==!1){if(e.simd==="relaxed"){if(!cg())throw new Error("Relaxed WebAssembly SIMD is not supported in the current environment.")}else if(!pg())throw new Error("WebAssembly SIMD is not supported in the current environment.")}let s=dg();a>1&&!s&&(typeof self<"u"&&!self.crossOriginIsolated&&console.warn("env.wasm.numThreads is set to "+a+", but this will not work unless you enable crossOriginIsolated mode. See https://web.dev/cross-origin-isolation-guide/ for more info."),console.warn("WebAssembly multi-threading is not supported in the current environment. Falling back to single-threading."),e.numThreads=a=1);let o=e.wasmPaths,p=typeof o=="string"?o:void 0,d=o==null?void 0:o.mjs,g=(d==null?void 0:d.href)??d,m=o==null?void 0:o.wasm,_=(m==null?void 0:m.href)??m,v=e.wasmBinary,[x,T]=await f0(g,p,a>1,!!v||!!_),C=!1,A=[];if(r>0&&A.push(new Promise(R=>{setTimeout(()=>{C=!0,R()},r)})),A.push(new Promise((R,H)=>{let U={numThreads:a};if(v)U.wasmBinary=v,U.locateFile=P=>P;else if(_||p)U.locateFile=P=>_??p+P;else if(g&&g.indexOf("blob:")!==0)U.locateFile=P=>new URL(P,g).href;else if(x){let P=c0();P&&(U.locateFile=F=>P+F)}T(U).then(P=>{zo=!1,qu=!0,yd=P,R(),x&&URL.revokeObjectURL(x)},P=>{zo=!1,_d=!0,H(P)})})),await Promise.race(A),C)throw new Error(`WebAssembly backend initializing failed due to timeout: ${r}ms`)},Wt=()=>{if(qu&&yd)return yd;throw new Error("WebAssembly is not initialized yet.")}}),Ni,rl,Pt,Ep=Ee(()=>{Fn(),Ni=(e,r)=>{let a=Wt(),s=a.lengthBytesUTF8(e)+1,o=a._malloc(s);return a.stringToUTF8(e,o,s),r.push(o),o},rl=(e,r,a,s)=>{if(typeof e=="object"&&e!==null){if(a.has(e))throw new Error("Circular reference in options");a.add(e)}Object.entries(e).forEach(([o,p])=>{let d=r?r+o:o;if(typeof p=="object")rl(p,d+".",a,s);else if(typeof p=="string"||typeof p=="number")s(d,p.toString());else if(typeof p=="boolean")s(d,p?"1":"0");else throw new Error(`Can't handle extra config type: ${typeof p}`)})},Pt=e=>{let r=Wt(),a=r.stackSave();try{let s=r.PTR_SIZE,o=r.stackAlloc(2*s);r._OrtGetLastError(o,o+s);let p=Number(r.getValue(o,s===4?"i32":"i64")),d=r.getValue(o+s,"*"),g=d?r.UTF8ToString(d):"";throw new Error(`${e} ERROR_CODE: ${p}, ERROR_MESSAGE: ${g}`)}finally{r.stackRestore(a)}}}),m0,Z2=Ee(()=>{Fn(),Ep(),m0=e=>{let r=Wt(),a=0,s=[],o=e||{};try{if((e==null?void 0:e.logSeverityLevel)===void 0)o.logSeverityLevel=2;else if(typeof e.logSeverityLevel!="number"||!Number.isInteger(e.logSeverityLevel)||e.logSeverityLevel<0||e.logSeverityLevel>4)throw new Error(`log severity level is not valid: ${e.logSeverityLevel}`);if((e==null?void 0:e.logVerbosityLevel)===void 0)o.logVerbosityLevel=0;else if(typeof e.logVerbosityLevel!="number"||!Number.isInteger(e.logVerbosityLevel))throw new Error(`log verbosity level is not valid: ${e.logVerbosityLevel}`);(e==null?void 0:e.terminate)===void 0&&(o.terminate=!1);let p=0;return(e==null?void 0:e.tag)!==void 0&&(p=Ni(e.tag,s)),a=r._OrtCreateRunOptions(o.logSeverityLevel,o.logVerbosityLevel,!!o.terminate,p),a===0&&Pt("Can't create run options."),(e==null?void 0:e.extra)!==void 0&&rl(e.extra,"",new WeakSet,(d,g)=>{let m=Ni(d,s),_=Ni(g,s);r._OrtAddRunConfigEntry(a,m,_)!==0&&Pt(`Can't set a run config entry: ${d} - ${g}.`)}),[a,s]}catch(p){throw a!==0&&r._OrtReleaseRunOptions(a),s.forEach(d=>r._free(d)),p}}}),hg,fg,mg,On,gg,g0,Q2=Ee(()=>{Fn(),Ep(),hg=e=>{switch(e){case"disabled":return 0;case"basic":return 1;case"extended":return 2;case"layout":return 3;case"all":return 99;default:throw new Error(`unsupported graph optimization level: ${e}`)}},fg=e=>{switch(e){case"sequential":return 0;case"parallel":return 1;default:throw new Error(`unsupported execution mode: ${e}`)}},mg=e=>{e.extra||(e.extra={}),e.extra.session||(e.extra.session={});let r=e.extra.session;r.use_ort_model_bytes_directly||(r.use_ort_model_bytes_directly="1"),e.executionProviders&&e.executionProviders.some(a=>(typeof a=="string"?a:a.name)==="webgpu")&&(e.enableMemPattern=!1)},On=(e,r,a,s)=>{let o=Ni(r,s),p=Ni(a,s);Wt()._OrtAddSessionConfigEntry(e,o,p)!==0&&Pt(`Can't set a session config entry: ${r} - ${a}.`)},gg=async(e,r,a)=>{let s=r.executionProviders;for(let o of s){let p=typeof o=="string"?o:o.name,d=[];switch(p){case"webnn":if(p="WEBNN",On(e,"session.disable_quant_qdq","1",a),On(e,"session.disable_qdq_constant_folding","1",a),typeof o!="string"){let x=o==null?void 0:o.deviceType;x&&On(e,"deviceType",x,a)}break;case"webgpu":if(p="JS",typeof o!="string"){let x=o;if(x!=null&&x.preferredLayout){if(x.preferredLayout!=="NCHW"&&x.preferredLayout!=="NHWC")throw new Error(`preferredLayout must be either 'NCHW' or 'NHWC': ${x.preferredLayout}`);On(e,"preferredLayout",x.preferredLayout,a)}}break;case"wasm":case"cpu":continue;default:throw new Error(`not supported execution provider: ${p}`)}let g=Ni(p,a),m=d.length,_=0,v=0;if(m>0){_=Wt()._malloc(m*Wt().PTR_SIZE),a.push(_),v=Wt()._malloc(m*Wt().PTR_SIZE),a.push(v);for(let x=0;x<m;x++)Wt().setValue(_+x*Wt().PTR_SIZE,d[x][0],"*"),Wt().setValue(v+x*Wt().PTR_SIZE,d[x][1],"*")}await Wt()._OrtAppendExecutionProvider(e,g,_,v,m)!==0&&Pt(`Can't append execution provider: ${p}.`)}},g0=async e=>{let r=Wt(),a=0,s=[],o=e||{};mg(o);try{let p=hg(o.graphOptimizationLevel??"all"),d=fg(o.executionMode??"sequential"),g=typeof o.logId=="string"?Ni(o.logId,s):0,m=o.logSeverityLevel??2;if(!Number.isInteger(m)||m<0||m>4)throw new Error(`log severity level is not valid: ${m}`);let _=o.logVerbosityLevel??0;if(!Number.isInteger(_)||_<0||_>4)throw new Error(`log verbosity level is not valid: ${_}`);let v=typeof o.optimizedModelFilePath=="string"?Ni(o.optimizedModelFilePath,s):0;if(a=r._OrtCreateSessionOptions(p,!!o.enableCpuMemArena,!!o.enableMemPattern,d,!!o.enableProfiling,0,g,m,_,v),a===0&&Pt("Can't create session options."),o.executionProviders&&await gg(a,o,s),o.enableGraphCapture!==void 0){if(typeof o.enableGraphCapture!="boolean")throw new Error(`enableGraphCapture must be a boolean value: ${o.enableGraphCapture}`);On(a,"enableGraphCapture",o.enableGraphCapture.toString(),s)}if(o.freeDimensionOverrides)for(let[x,T]of Object.entries(o.freeDimensionOverrides)){if(typeof x!="string")throw new Error(`free dimension override name must be a string: ${x}`);if(typeof T!="number"||!Number.isInteger(T)||T<0)throw new Error(`free dimension override value must be a non-negative integer: ${T}`);let C=Ni(x,s);r._OrtAddFreeDimensionOverride(a,C,T)!==0&&Pt(`Can't set a free dimension override: ${x} - ${T}.`)}return o.extra!==void 0&&rl(o.extra,"",new WeakSet,(x,T)=>{On(a,x,T,s)}),[a,s]}catch(p){throw a!==0&&r._OrtReleaseSessionOptions(a)!==0&&Pt("Can't release session options."),s.forEach(d=>r._free(d)),p}}}),Pn,Sa,Un,dl,il,Ip,zp,np,ut=Ee(()=>{Pn=e=>{switch(e){case"int8":return 3;case"uint8":return 2;case"bool":return 9;case"int16":return 5;case"uint16":return 4;case"int32":return 6;case"uint32":return 12;case"float16":return 10;case"float32":return 1;case"float64":return 11;case"string":return 8;case"int64":return 7;case"uint64":return 13;case"int4":return 22;case"uint4":return 21;default:throw new Error(`unsupported data type: ${e}`)}},Sa=e=>{switch(e){case 3:return"int8";case 2:return"uint8";case 9:return"bool";case 5:return"int16";case 4:return"uint16";case 6:return"int32";case 12:return"uint32";case 10:return"float16";case 1:return"float32";case 11:return"float64";case 8:return"string";case 7:return"int64";case 13:return"uint64";case 22:return"int4";case 21:return"uint4";default:throw new Error(`unsupported data type: ${e}`)}},Un=(e,r)=>{let a=[-1,4,1,1,2,2,4,8,-1,1,2,8,4,8,-1,-1,-1,-1,-1,-1,-1,.5,.5][e],s=typeof r=="number"?r:r.reduce((o,p)=>o*p,1);return a>0?Math.ceil(s*a):void 0},dl=e=>{switch(e){case"float16":return typeof Float16Array<"u"&&Float16Array.from?Float16Array:Uint16Array;case"float32":return Float32Array;case"uint8":return Uint8Array;case"int8":return Int8Array;case"uint16":return Uint16Array;case"int16":return Int16Array;case"int32":return Int32Array;case"bool":return Uint8Array;case"float64":return Float64Array;case"uint32":return Uint32Array;case"int64":return BigInt64Array;case"uint64":return BigUint64Array;default:throw new Error(`unsupported type: ${e}`)}},il=e=>{switch(e){case"verbose":return 0;case"info":return 1;case"warning":return 2;case"error":return 3;case"fatal":return 4;default:throw new Error(`unsupported logging level: ${e}`)}},Ip=e=>e==="float32"||e==="float16"||e==="int32"||e==="int64"||e==="uint32"||e==="uint8"||e==="bool"||e==="uint4"||e==="int4",zp=e=>e==="float32"||e==="float16"||e==="int32"||e==="int64"||e==="uint32"||e==="uint64"||e==="int8"||e==="uint8"||e==="bool"||e==="uint4"||e==="int4",np=e=>{switch(e){case"none":return 0;case"cpu":return 1;case"cpu-pinned":return 2;case"texture":return 3;case"gpu-buffer":return 4;case"ml-tensor":return 5;default:throw new Error(`unsupported data location: ${e}`)}}}),Cp,y0=Ee(()=>{Sp(),Cp=async e=>{if(typeof e=="string"){let r=await fetch(e);if(!r.ok)throw new Error(`failed to load external data file: ${e}`);let a=r.headers.get("Content-Length"),s=a?parseInt(a,10):0;if(s<1073741824)return new Uint8Array(await r.arrayBuffer());{if(!r.body)throw new Error(`failed to load external data file: ${e}, no response body.`);let o=r.body.getReader(),p;try{p=new ArrayBuffer(s)}catch(g){if(g instanceof RangeError){let m=Math.ceil(s/65536);p=new WebAssembly.Memory({initial:m,maximum:m}).buffer}else throw g}let d=0;for(;;){let{done:g,value:m}=await o.read();if(g)break;let _=m.byteLength;new Uint8Array(p,d,_).set(m),d+=_}return new Uint8Array(p,0,s)}}else return e instanceof Blob?new Uint8Array(await e.arrayBuffer()):e instanceof Uint8Array?e:new Uint8Array(e)}}),yg,_g,wg,bg,Ap,$g,It,Ta=Ee(()=>{ut(),yg=["V","I","W","E","F"],_g=(e,r)=>{console.log(`[${yg[e]},${new Date().toISOString()}]${r}`)},Ap=(e,r)=>{wg=e,bg=r},$g=(e,r)=>{let a=il(e),s=il(wg);a>=s&&_g(a,typeof r=="function"?r():r)},It=(...e)=>{bg&&$g(...e)}}),vg,ms,ge,al,_0,w0,b0,ct=Ee(()=>{vg=class{static calcMatMulShape(e,r){return e[1]!==r[0]?void 0:[e[0],r[1]]}},ms=class{static calcShape(e,r,a=!1){let s=e.length,o=r.length;if(s===0)return r;if(o===0)return e;let p=Math.max(e.length,r.length),d=new Array(p);if(a){if(s<2||o<2)return;let g=vg.calcMatMulShape([e[s-2],e[s-1]],[r[o-2],r[o-1]]);if(g===void 0)return;[d[p-2],d[p-1]]=g}for(let g=a?3:1;g<=p;g++){let m=s-g<0?1:e[s-g],_=o-g<0?1:r[o-g];if(m!==_&&m>1&&_>1)return;let v=Math.max(m,_);if(m&&_)d[p-g]=Math.max(m,_);else{if(v>1)return;d[p-g]=0}}return d}static isValidBroadcast(e,r){let a=e.length,s=r.length;if(a>s)return!1;for(let o=1;o<=a;o++)if(e[a-o]!==1&&e[a-o]!==r[s-o])return!1;return!0}},ge=class Ju{static size(r){return Ju.getSizeFromDimensionRange(r,0,r.length)}static convertShape(r,a=4){let s=r.length;if(s===0)return[];let o=new Array(s),p=s-1;for(;p>=0;){if(r[p]%a===0){o[p]=r[p]/a;break}if(a%r[p]!==0)throw new Error("cannot convert shape");o[p]=1,a/=r[p],p--}for(p--;p>=0;p--)o[p]=r[p];return o}static sizeFromDimension(r,a){if(a<0||a>r.length)throw new Error(`invalid dimension of ${a} for sizeFromDimension as Tensor has ${r.length} dimensions.`);return Ju.getSizeFromDimensionRange(r,a,r.length)}static sizeToDimension(r,a){if(a<0||a>r.length)throw new Error(`invalid dimension of ${a} for sizeToDimension as Tensor has ${r.length} dimensions.`);return Ju.getSizeFromDimensionRange(r,0,a)}static getSizeFromDimensionRange(r,a,s){let o=1;for(let p=a;p<s;p++){if(r[p]<0)throw new Error("cannot get valid size from specified dimension range. Most likely the range contains negative values in them.");o*=Number(r[p])}return o}static computeStrides(r){let a=r.length;if(a===0)return[];if(a===1)return[1];let s=new Array(a);s[a-1]=1,s[a-2]=r[a-1];for(let o=a-3;o>=0;--o)s[o]=s[o+1]*r[o+1];return s}static normalizeAxis(r,a){if(r<-a&&r>=a)throw new Error("unsupported axis for this operation.");return r<0?r+a:r}static normalizeAxes(r,a){return r.map(s=>this.normalizeAxis(s,a??r.length))}static sortBasedOnPerm(r,a){return a?a.map(s=>r[s]):r.slice().reverse()}static padShape(r,a){let s=r.length;return r.map((o,p)=>o+a[p]+a[p+s])}static areEqual(r,a){return r.length!==a.length?!1:r.every((s,o)=>s===a[o])}},al=class Lo{static adjustPoolAttributes(r,a,s,o,p,d){if(!r&&s.length!==a.length-2)throw new Error("length of specified kernel shapes should be 2 less than length of input dimensions");if(r)for(let g=0;g<a.length-2;g++)g>=s.length?s.push(a[g+2]):s[g]=a[g+2];for(let g=0;g<s.length;g++)if(g<o.length){if(o[g]<0)throw new Error("strides should be greater than or equal to 1")}else o.push(1);for(let g=0;g<s.length;g++)if(g<p.length){if(p[g]<0)throw new Error("dilations should be greater than or equal to 1")}else p.push(1);for(let g=0;g<s.length*2;g++)if(g<d.length){if(d[g]<0)throw new Error("pad should be greater than or equal to 1")}else d.push(0);for(let g=0;g<s.length;g++){if(s[g]<=0)throw new Error("kernel shapes need to be greater than 0");if(d[g]>=s[g]||d[g+s.length]>=s[g])throw new Error("pads should be smaller than kernel")}}static adjustPadsBasedOnAutoPad(r,a,s,o,p,d,g){if(g){if(p.length!==2*(r.length-2))throw new Error("length of pads should be twice the length of data dimensions");if(a.length!==r.length-2)throw new Error("length of strides should be the length of data dimensions");if(o.length!==r.length-2)throw new Error("length of kernel shapes should be the length of data dimensions");for(let m=0;m<r.length-2;m++)Lo.adjustPadAndReturnShape(r[m+(d?1:2)],a[m],s[m],o[m],p,m,m+r.length-2,g)}}static computePoolOutputShape(r,a,s,o,p,d,g){if(a.length<=0)throw new Error("input shape must be of size greater than 0");let m=[a[0],a[1]];return Lo.computeShapeHelper(r,a,m,s,o,p,d,g),m}static computeConvOutputShape(r,a,s,o,p,d,g){if(r.length<=0||a.length<=0)throw new Error("invalid input tensor dims or invalid filter tensor dims");let m=[r[0],a[0]];return Lo.computeShapeHelper(!1,r,m,s,o,p,d,g),m}static computeShapeHelper(r,a,s,o,p,d,g,m){if(r)for(let _=0;_<a.length-2;_++)s.push(1);else for(let _=0;_<a.length-2;_++)s.push(Lo.adjustPadAndReturnShape(a[_+2],o[_],p[_],d[_],g,_,_+a.length-2,m))}static adjustPadAndReturnShape(r,a,s,o,p,d,g,m){let _=s*(o-1)+1;if(m&&m!=="NOTSET")switch(m){case"VALID":return p[d]=0,p[g]=0,Math.floor((r-_)/a+1);case"SAME_LOWER":case"SAME_UPPER":if(s!==1)throw new Error("Dilation not supported for SAME_UPPER or SAME_LOWER");{let v=((r+a-1)/a-1)*a+o-r;return p[d]=Math.floor(m==="SAME_LOWER"?(v+1)/2:v/2),p[g]=v-p[d],Math.floor((r+v-o)/a+1)}default:throw new Error("Unsupported AutoPad type")}else return Math.floor((r+p[d]+p[g]-_)/a+1)}},_0=class{static getShapeOfGemmResult(e,r,a,s,o){if(e.length!==2||a.length!==2)throw new Error("shape need to be of size 2");let p,d,g;r?(p=e[1],d=e[0]):(p=e[0],d=e[1]);let m=-1;if(s?(g=a[0],m=1):(g=a[1],m=0),a[m]!==d)throw new Error("dimension mismatch");if(p<=0||g<=0||d<=0)throw new Error("invalid shape specified");if(o&&!ms.isValidBroadcast(o,[p,g]))throw new Error("gemm: invalid bias shape for broadcast");return[p,g,d]}},w0=-34028234663852886e22,b0=34028234663852886e22}),Op,$0=Ee(()=>{ut(),Op=(e,r)=>new(dl(r))(e)}),wd,sp,bd,xg,$d,Sg,vd,xd,Sd,Tg,v0,X2=Ee(()=>{ut(),Ta(),wd=new Map([["float32",32],["float16",16],["int32",32],["uint32",32],["int64",64],["uint64",64],["int8",8],["uint8",8],["int4",4],["uint4",4]]),sp=(e,r)=>{if(r==="int32")return e;let a=wd.get(r);if(!a)throw new Error(`WebNN backend does not support data type: ${r}`);let s=a/8;if(e.byteLength%s!==0)throw new Error(`Invalid Uint8Array length - must be a multiple of ${s}.`);let o=e.byteLength/s,p=new(dl(r))(e.buffer,e.byteOffset,o);switch(r){case"int64":case"uint64":{let d=new Int32Array(o);for(let g=0;g<o;g++){let m=p[g];if(m>2147483647n||m<-2147483648n)throw new Error("Can not convert int64 data to int32 - value out of range.");d[g]=Number(m)}return new Uint8Array(d.buffer)}case"int8":case"uint8":case"uint32":{if(r==="uint32"&&p.some(g=>g>2147483647))throw new Error("Can not convert uint32 data to int32 - value out of range.");let d=Int32Array.from(p,Number);return new Uint8Array(d.buffer)}default:throw new Error(`Unsupported data conversion from ${r} to 'int32'`)}},bd=(e,r)=>{if(r==="int32")return e;if(e.byteLength%4!==0)throw new Error("Invalid Uint8Array length - must be a multiple of 4 (int32).");let a=e.byteLength/4,s=new Int32Array(e.buffer,e.byteOffset,a);switch(r){case"int64":{let o=BigInt64Array.from(s,BigInt);return new Uint8Array(o.buffer)}case"uint64":{if(s.some(p=>p<0))throw new Error("Can not convert int32 data to uin64 - negative value found.");let o=BigUint64Array.from(s,BigInt);return new Uint8Array(o.buffer)}case"int8":{if(s.some(p=>p<-128||p>127))throw new Error("Can not convert int32 data to int8 - value out of range.");let o=Int8Array.from(s,Number);return new Uint8Array(o.buffer)}case"uint8":{if(s.some(o=>o<0||o>255))throw new Error("Can not convert int32 data to uint8 - value out of range.");return Uint8Array.from(s,Number)}case"uint32":{if(s.some(p=>p<0))throw new Error("Can not convert int32 data to uint32 - negative value found.");let o=Uint32Array.from(s,Number);return new Uint8Array(o.buffer)}default:throw new Error(`Unsupported data conversion from 'int32' to ${r}`)}},xg=1,$d=()=>xg++,Sg=new Map([["int8","int32"],["uint8","int32"],["uint32","int32"],["int64","int32"]]),vd=(e,r)=>{let a=wd.get(e);if(!a)throw new Error(`WebNN backend does not support data type: ${e}`);return r.length>0?Math.ceil(r.reduce((s,o)=>s*o)*a/8):0},xd=class{constructor(e){this.isDataConverted=!1;let{sessionId:r,context:a,tensor:s,dataType:o,shape:p,fallbackDataType:d}=e;this.sessionId=r,this.mlContext=a,this.mlTensor=s,this.dataType=o,this.tensorShape=p,this.fallbackDataType=d}get tensor(){return this.mlTensor}get type(){return this.dataType}get fallbackType(){return this.fallbackDataType}get shape(){return this.tensorShape}get byteLength(){return vd(this.dataType,this.tensorShape)}destroy(){It("verbose",()=>"[WebNN] TensorWrapper.destroy"),this.mlTensor.destroy()}write(e){this.mlContext.writeTensor(this.mlTensor,e)}async read(e){if(this.fallbackDataType){let r=await this.mlContext.readTensor(this.mlTensor),a=bd(new Uint8Array(r),this.dataType);if(e){(e instanceof ArrayBuffer?new Uint8Array(e):new Uint8Array(e.buffer,e.byteOffset,e.byteLength)).set(a);return}else return a.buffer}else return e?this.mlContext.readTensor(this.mlTensor,e):this.mlContext.readTensor(this.mlTensor)}canReuseTensor(e,r,a){return this.mlContext===e&&this.dataType===r&&this.tensorShape.length===a.length&&this.tensorShape.every((s,o)=>s===a[o])}setIsDataConverted(e){this.isDataConverted=e}},Sd=class{constructor(e,r){this.tensorManager=e,this.wrapper=r}get tensorWrapper(){return this.wrapper}releaseTensor(){this.tensorWrapper&&(this.tensorManager.releaseTensor(this.tensorWrapper),this.wrapper=void 0)}async ensureTensor(e,r,a,s){let o=this.tensorManager.getMLContext(e),p=this.tensorManager.getMLOpSupportLimits(e),d;if(!(p!=null&&p.input.dataTypes.includes(r))){if(d=Sg.get(r),!d||(p==null?void 0:p.input.dataTypes.includes(d)))throw new Error(`WebNN backend does not support data type: ${r}`);It("verbose",()=>`[WebNN] TensorIdTracker.ensureTensor: fallback dataType from ${r} to ${d}`)}if(this.wrapper){if(this.wrapper.canReuseTensor(o,r,a))return this.wrapper.tensor;if(s){if(this.wrapper.byteLength!==vd(r,a))throw new Error("Unable to copy data to tensor with different size.");this.activeUpload=new Uint8Array(await this.wrapper.read())}this.tensorManager.releaseTensor(this.wrapper)}let g=typeof MLTensorUsage>"u"?void 0:MLTensorUsage.READ|MLTensorUsage.WRITE;return this.wrapper=await this.tensorManager.getCachedTensor(e,r,a,g,!0,!0,d),s&&this.activeUpload&&(this.wrapper.write(this.activeUpload),this.activeUpload=void 0),this.wrapper.tensor}upload(e){let r=e;if(this.wrapper){if(this.wrapper.fallbackType)if(this.wrapper.fallbackType==="int32")r=sp(e,this.wrapper.type),this.wrapper.setIsDataConverted(!0);else throw new Error(`Unsupported fallback data type: ${this.wrapper.fallbackType}`);if(e.byteLength===this.wrapper.byteLength){this.wrapper.write(r);return}else It("verbose",()=>"Data size does not match tensor size. Releasing tensor."),this.releaseTensor()}this.activeUpload?this.activeUpload.set(r):this.activeUpload=new Uint8Array(r)}async download(e){var r,a;if(this.activeUpload){let s=(r=this.wrapper)!=null&&r.isDataConverted?bd(this.activeUpload,(a=this.wrapper)==null?void 0:a.type):this.activeUpload;if(e){e instanceof ArrayBuffer?new Uint8Array(e).set(s):new Uint8Array(e.buffer,e.byteOffset,e.byteLength).set(s);return}else return s.buffer}if(!this.wrapper)throw new Error("Tensor has not been created.");return e?this.wrapper.read(e):this.wrapper.read()}},Tg=class{constructor(e){this.backend=e,this.tensorTrackersById=new Map,this.freeTensors=[],this.externalTensors=new Set}getMLContext(e){let r=this.backend.getMLContext(e);if(!r)throw new Error("MLContext not found for session.");return r}getMLOpSupportLimits(e){return this.backend.getMLOpSupportLimits(e)}reserveTensorId(){let e=$d();return this.tensorTrackersById.set(e,new Sd(this)),e}releaseTensorId(e){let r=this.tensorTrackersById.get(e);r&&(this.tensorTrackersById.delete(e),r.tensorWrapper&&this.releaseTensor(r.tensorWrapper))}async ensureTensor(e,r,a,s,o){It("verbose",()=>`[WebNN] TensorManager.ensureTensor {tensorId: ${r}, dataType: ${a}, shape: ${s}, copyOld: ${o}}`);let p=this.tensorTrackersById.get(r);if(!p)throw new Error("Tensor not found.");return p.ensureTensor(e,a,s,o)}upload(e,r){let a=this.tensorTrackersById.get(e);if(!a)throw new Error("Tensor not found.");a.upload(r)}async download(e,r){It("verbose",()=>`[WebNN] TensorManager.download {tensorId: ${e}, dstBuffer: ${r==null?void 0:r.byteLength}}`);let a=this.tensorTrackersById.get(e);if(!a)throw new Error("Tensor not found.");return a.download(r)}releaseTensorsForSession(e){for(let r of this.freeTensors)r.sessionId===e&&r.destroy();this.freeTensors=this.freeTensors.filter(r=>r.sessionId!==e)}registerTensor(e,r,a,s){let o=this.getMLContext(e),p=$d(),d=new xd({sessionId:e,context:o,tensor:r,dataType:a,shape:s});return this.tensorTrackersById.set(p,new Sd(this,d)),this.externalTensors.add(d),p}async getCachedTensor(e,r,a,s,o,p,d){let g=this.getMLContext(e);for(let[_,v]of this.freeTensors.entries())if(v.canReuseTensor(g,r,a)){It("verbose",()=>`[WebNN] Reusing tensor {dataType: ${r}, ${d?`fallbackDataType: ${d},`:""} shape: ${a}`);let x=this.freeTensors.splice(_,1)[0];return x.sessionId=e,x}It("verbose",()=>`[WebNN] MLContext.createTensor {dataType: ${r}, ${d?`fallbackDataType: ${d},`:""} shape: ${a}}`);let m=await g.createTensor({dataType:d??r,shape:a,dimensions:a,usage:s,writable:o,readable:p});return new xd({sessionId:e,context:g,tensor:m,dataType:r,shape:a,fallbackDataType:d})}releaseTensor(e){this.externalTensors.has(e)&&this.externalTensors.delete(e),this.freeTensors.push(e)}},v0=(...e)=>new Tg(...e)}),Co,kg,x0,Y2=Ee(()=>{ut(),Fn(),$0(),X2(),Ta(),Co=new Map([[1,"float32"],[10,"float16"],[6,"int32"],[12,"uint32"],[7,"int64"],[13,"uint64"],[22,"int4"],[21,"uint4"],[3,"int8"],[2,"uint8"],[9,"uint8"]]),kg=(e,r)=>{if(e===r)return!0;if(e===void 0||r===void 0)return!1;let a=Object.keys(e).sort(),s=Object.keys(r).sort();return a.length===s.length&&a.every((o,p)=>o===s[p]&&e[o]===r[o])},x0=class{constructor(e){this.tensorManager=v0(this),this.mlContextBySessionId=new Map,this.sessionIdsByMLContext=new Map,this.mlContextCache=[],this.sessionGraphInputs=new Map,this.sessionGraphOutputs=new Map,this.temporaryGraphInputs=[],this.temporaryGraphOutputs=[],this.temporarySessionTensorIds=new Map,this.mlOpSupportLimitsBySessionId=new Map,Ap(e.logLevel,!!e.debug)}get currentSessionId(){if(this.activeSessionId===void 0)throw new Error("No active session");return this.activeSessionId}onRunStart(e){It("verbose",()=>`[WebNN] onRunStart {sessionId: ${e}}`),this.activeSessionId=e}onRunEnd(e){It("verbose",()=>`[WebNN] onRunEnd {sessionId: ${e}}`);let r=this.temporarySessionTensorIds.get(e);if(r){for(let a of r)It("verbose",()=>`[WebNN] releasing temporary tensor {tensorId: ${a}}`),this.tensorManager.releaseTensorId(a);this.temporarySessionTensorIds.delete(e),this.activeSessionId=void 0}}async createMLContext(e){if(e instanceof GPUDevice){let a=this.mlContextCache.findIndex(s=>s.gpuDevice===e);if(a!==-1)return this.mlContextCache[a].mlContext;{let s=await navigator.ml.createContext(e);return this.mlContextCache.push({gpuDevice:e,mlContext:s}),s}}else if(e===void 0){let a=this.mlContextCache.findIndex(s=>s.options===void 0&&s.gpuDevice===void 0);if(a!==-1)return this.mlContextCache[a].mlContext;{let s=await navigator.ml.createContext();return this.mlContextCache.push({mlContext:s}),s}}let r=this.mlContextCache.findIndex(a=>kg(a.options,e));if(r!==-1)return this.mlContextCache[r].mlContext;{let a=await navigator.ml.createContext(e);return this.mlContextCache.push({options:e,mlContext:a}),a}}registerMLContext(e,r){this.mlContextBySessionId.set(e,r);let a=this.sessionIdsByMLContext.get(r);a||(a=new Set,this.sessionIdsByMLContext.set(r,a)),a.add(e),this.mlOpSupportLimitsBySessionId.has(e)||this.mlOpSupportLimitsBySessionId.set(e,r.opSupportLimits()),this.temporaryGraphInputs.length>0&&(this.sessionGraphInputs.set(e,this.temporaryGraphInputs),this.temporaryGraphInputs=[]),this.temporaryGraphOutputs.length>0&&(this.sessionGraphOutputs.set(e,this.temporaryGraphOutputs),this.temporaryGraphOutputs=[])}onReleaseSession(e){this.sessionGraphInputs.delete(e),this.sessionGraphOutputs.delete(e);let r=this.mlContextBySessionId.get(e);if(!r)return;this.tensorManager.releaseTensorsForSession(e),this.mlContextBySessionId.delete(e),this.mlOpSupportLimitsBySessionId.delete(e);let a=this.sessionIdsByMLContext.get(r);if(a.delete(e),a.size===0){this.sessionIdsByMLContext.delete(r);let s=this.mlContextCache.findIndex(o=>o.mlContext===r);s!==-1&&this.mlContextCache.splice(s,1)}}getMLContext(e){return this.mlContextBySessionId.get(e)}getMLOpSupportLimits(e){return this.mlOpSupportLimitsBySessionId.get(e)}reserveTensorId(){return this.tensorManager.reserveTensorId()}releaseTensorId(e){It("verbose",()=>`[WebNN] releaseTensorId {tensorId: ${e}}`),this.tensorManager.releaseTensorId(e)}async ensureTensor(e,r,a,s,o){let p=Co.get(a);if(!p)throw new Error(`Unsupported ONNX data type: ${a}`);return this.tensorManager.ensureTensor(e??this.currentSessionId,r,p,s,o)}async createTemporaryTensor(e,r,a){It("verbose",()=>`[WebNN] createTemporaryTensor {onnxDataType: ${r}, shape: ${a}}`);let s=Co.get(r);if(!s)throw new Error(`Unsupported ONNX data type: ${r}`);let o=this.tensorManager.reserveTensorId();await this.tensorManager.ensureTensor(e,o,s,a,!1);let p=this.temporarySessionTensorIds.get(e);return p?p.push(o):this.temporarySessionTensorIds.set(e,[o]),o}uploadTensor(e,r){if(!Wt().shouldTransferToMLTensor)throw new Error("Trying to upload to a MLTensor while shouldTransferToMLTensor is false");It("verbose",()=>`[WebNN] uploadTensor {tensorId: ${e}, data: ${r.byteLength}}`),this.tensorManager.upload(e,r)}async downloadTensor(e,r){return this.tensorManager.download(e,r)}createMLTensorDownloader(e,r){return async()=>{let a=await this.tensorManager.download(e);return Op(a,r)}}registerMLTensor(e,r,a,s){let o=Co.get(a);if(!o)throw new Error(`Unsupported ONNX data type: ${a}`);let p=this.tensorManager.registerTensor(e,r,o,s);return It("verbose",()=>`[WebNN] registerMLTensor {tensor: ${r}, dataType: ${o}, dimensions: ${s}} -> {tensorId: ${p}}`),p}registerMLConstant(e,r,a,s,o,p,d=!1){if(!p)throw new Error("External mounted files are not available.");let g=e;e.startsWith("./")&&(g=e.substring(2));let m=p.get(g);if(!m)throw new Error(`File with name ${g} not found in preloaded files.`);if(r+a>m.byteLength)throw new Error("Out of bounds: data offset and length exceed the external file data size.");let _=m.slice(r,r+a).buffer,v;switch(o.dataType){case"float32":v=new Float32Array(_);break;case"float16":v=typeof Float16Array<"u"&&Float16Array.from?new Float16Array(_):new Uint16Array(_);break;case"int32":v=new Int32Array(_);break;case"uint32":v=new Uint32Array(_);break;case"int64":if(d){let x=sp(new Uint8Array(_),"int64");v=new Int32Array(x.buffer),o.dataType="int32"}else v=new BigInt64Array(_);break;case"uint64":v=new BigUint64Array(_);break;case"int8":v=new Int8Array(_);break;case"int4":case"uint4":case"uint8":v=new Uint8Array(_);break;default:throw new Error(`Unsupported data type: ${o.dataType} in creating WebNN Constant from external data.`)}return It("verbose",()=>`[WebNN] registerMLConstant {dataType: ${o.dataType}, shape: ${o.shape}}} ${d?"(Note: it was int64 data type and registered to int32 as workaround)":""}`),s.constant(o,v)}registerGraphInput(e){this.temporaryGraphInputs.push(e)}registerGraphOutput(e){this.temporaryGraphOutputs.push(e)}isGraphInput(e,r){let a=this.sessionGraphInputs.get(e);return a?a.includes(r):!1}isGraphOutput(e,r){let a=this.sessionGraphOutputs.get(e);return a?a.includes(r):!1}isGraphInputOutputTypeSupported(e,r,a=!0){let s=Co.get(Pn(r)),o=this.mlOpSupportLimitsBySessionId.get(e);return typeof s>"u"?!1:a?!!(o!=null&&o.input.dataTypes.includes(s)):!!(o!=null&&o.output.dataTypes.includes(s))}flush(){}}}),Rp=Ee(()=>{}),Td,Vu,Wu,Eg,Ig,kd,op,zg,S0,J2=Ee(()=>{Ta(),Rp(),Td=new Map([[64,250],[128,200],[256,200],[512,200],[2048,230],[4096,200],[8192,50],[16384,50],[32768,50],[65536,50],[131072,50],[262144,50],[524288,50],[1048576,50],[2097152,30],[4194304,20],[8388608,10],[12582912,10],[16777216,10],[26214400,15],[33554432,22],[44236800,2],[58982400,6],[67108864,6],[134217728,6],[167772160,6]]),Vu=[],Wu=e=>Math.ceil(Number(e)/16)*16,Eg=e=>{for(let r=0;r<Vu.length;r++){let a=Vu[r];if(e<=a)return a}return Math.ceil(e/16)*16},Ig=1,kd=()=>Ig++,op=async(e,r,a,s)=>{let o=Wu(a),p=e.device.createBuffer({size:o,usage:GPUBufferUsage.COPY_DST|GPUBufferUsage.MAP_READ});try{let d=e.getCommandEncoder();e.endComputePass(),d.copyBufferToBuffer(r,0,p,0,o),e.flush(),await p.mapAsync(GPUMapMode.READ);let g=p.getMappedRange();if(s){let m=s();return m.set(new Uint8Array(g,0,a)),m}else return new Uint8Array(g.slice(0,a))}finally{p.destroy()}},zg=class{constructor(e){this.backend=e,this.storageCache=new Map,this.freeBuffers=new Map,this.freeUniformBuffers=new Map,this.buffersPending=[],this.capturedPendingBuffers=new Map;for(let[r]of Td)Vu.push(r),this.freeBuffers.set(r,[]),this.freeUniformBuffers.set(r,[]);this.sessionCount=0}upload(e,r){let a=r.buffer,s=r.byteOffset,o=r.byteLength,p=Wu(o),d=this.storageCache.get(e);if(!d)throw new Error("gpu data for uploading does not exist");if(Number(d.originalSize)!==o)throw new Error(`inconsistent data size. gpu data size=${d.originalSize}, data size=${o}`);let g=this.backend.device.createBuffer({mappedAtCreation:!0,size:p,usage:GPUBufferUsage.MAP_WRITE|GPUBufferUsage.COPY_SRC}),m=g.getMappedRange();new Uint8Array(m).set(new Uint8Array(a,s,o)),g.unmap();let _=this.backend.device.createCommandEncoder();_.copyBufferToBuffer(g,0,d.gpuData.buffer,0,p),this.backend.device.queue.submit([_.finish()]),g.destroy(),It("verbose",()=>`[WebGPU] GpuDataManager.upload(id=${e})`)}memcpy(e,r){let a=this.storageCache.get(e);if(!a)throw new Error("source gpu data for memcpy does not exist");let s=this.storageCache.get(r);if(!s)throw new Error("destination gpu data for memcpy does not exist");if(a.originalSize!==s.originalSize)throw new Error("inconsistent source and destination gpu data size");let o=Wu(a.originalSize),p=this.backend.getCommandEncoder();this.backend.endComputePass(),p.copyBufferToBuffer(a.gpuData.buffer,0,s.gpuData.buffer,0,o)}registerExternalBuffer(e,r,a){let s;if(a){if(s=a[0],e===a[1])return It("verbose",()=>`[WebGPU] GpuDataManager.registerExternalBuffer(size=${r}) => id=${s}, buffer is the same, skip.`),s;if(this.backend.capturedCommandList.has(this.backend.currentSessionId))throw new Error(`Registering a different external buffer under graph capture mode is not supported yet.
             Please use the previous external buffer!`)}else s=kd();return this.storageCache.set(s,{gpuData:{id:s,type:0,buffer:e},originalSize:r}),It("verbose",()=>`[WebGPU] GpuDataManager.registerExternalBuffer(size=${r}) => id=${s}, registered.`),s}unregisterExternalBuffer(e){e!==void 0&&(this.storageCache.delete(e),It("verbose",()=>`[WebGPU] GpuDataManager.unregisterExternalBuffer() => id=${e}`))}create(e,r=GPUBufferUsage.STORAGE|GPUBufferUsage.COPY_SRC|GPUBufferUsage.COPY_DST){let a=Eg(e),s,o=(r&GPUBufferUsage.STORAGE)===GPUBufferUsage.STORAGE,p=(r&GPUBufferUsage.UNIFORM)===GPUBufferUsage.UNIFORM;if(o||p){let g=(o?this.freeBuffers:this.freeUniformBuffers).get(a);g?g.length>0?s=g.pop():s=this.backend.device.createBuffer({size:a,usage:r}):s=this.backend.device.createBuffer({size:a,usage:r})}else s=this.backend.device.createBuffer({size:a,usage:r});let d={id:kd(),type:0,buffer:s};return this.storageCache.set(d.id,{gpuData:d,originalSize:Number(e)}),It("verbose",()=>`[WebGPU] GpuDataManager.create(size=${e}) => id=${d.id}`),d}get(e){var r;return(r=this.storageCache.get(e))==null?void 0:r.gpuData}release(e){let r=typeof e=="bigint"?Number(e):e,a=this.storageCache.get(r);if(!a){if(this.storageCache.size===0)return 0;throw new Error("releasing data does not exist")}return It("verbose",()=>`[WebGPU] GpuDataManager.release(id=${r}), gpuDataId=${a.gpuData.id}`),this.storageCache.delete(r),this.buffersPending.push(a.gpuData.buffer),a.originalSize}async download(e,r){let a=this.storageCache.get(Number(e));if(!a)throw new Error("data does not exist");await op(this.backend,a.gpuData.buffer,a.originalSize,r)}refreshPendingBuffers(){if(this.buffersPending.length!==0)if(this.backend.sessionStatus==="default"){for(let e of this.buffersPending){let r=Td.get(e.size);if((e.usage&GPUBufferUsage.STORAGE)===GPUBufferUsage.STORAGE){let a=this.freeBuffers.get(e.size)||[];r===void 0||a.length>=r?e.destroy():a.push(e)}else if((e.usage&GPUBufferUsage.UNIFORM)===GPUBufferUsage.UNIFORM){let a=this.freeUniformBuffers.get(e.size)||[];r===void 0||a.length>=r?e.destroy():a.push(e)}else e.destroy()}this.buffersPending=[]}else{let e=this.capturedPendingBuffers.get(this.backend.currentSessionId);e||(e=[],this.capturedPendingBuffers.set(this.backend.currentSessionId,e));for(let r of this.buffersPending)e.push(r);this.buffersPending=[]}}dispose(){this.freeBuffers.forEach(e=>{e.forEach(r=>{r.destroy()})}),this.freeUniformBuffers.forEach(e=>{e.forEach(r=>{r.destroy()})}),this.storageCache.forEach(e=>{e.gpuData.buffer.destroy()}),this.capturedPendingBuffers.forEach(e=>{e.forEach(r=>{r.destroy()})}),this.storageCache=new Map,this.freeBuffers=new Map,this.freeUniformBuffers=new Map,this.capturedPendingBuffers=new Map}onCreateSession(){this.sessionCount+=1}onReleaseSession(e){let r=this.capturedPendingBuffers.get(e);r&&(r.forEach(a=>{a.destroy()}),this.capturedPendingBuffers.delete(e)),this.sessionCount-=1,this.sessionCount===0&&(It("warning",()=>"[WebGPU] Clearing webgpu buffer cache"),this.storageCache.forEach(a=>{a.gpuData.buffer.destroy()}),this.storageCache=new Map)}},S0=(...e)=>new zg(...e)}),Cg,Nt,Jt=Ee(()=>{Cg=class{constructor(e){Object.assign(this,e)}get cacheKey(){return this.key||(this.key=Object.getOwnPropertyNames(this).sort().map(e=>`${this[e]}`).join(";")),this.key}},Nt=e=>new Cg(e)}),gs,Gu,yr,Or,Je,Yt,up,fs,an,Qe,Ao,$e,je,T0,Bp,Ag,k0,ft=Ee(()=>{ut(),ct(),gs=64,Gu=(e,r)=>{if(r===3)throw new Error("vec3 has same alignment as vec4, use vec4 instead");switch(Number(e)){case 10:return r>1?`vec${r}<f16>`:"f16";case 1:return r>1?`vec${r}<f32>`:"f32";case 6:return r>1?`vec${r}<i32>`:"i32";case 12:return r>1?`vec${r}<u32>`:"u32";case 7:if(r>1)throw new Error("currently not supported vecX of uint64 yet");return["vec2<u32>","i32"];case 13:if(r>1)throw new Error("currently not supported vecX of uint64 yet");return["vec2<u32>","u32"];case 9:if(r!==4)throw new Error("bool must be vec4");return["u32","vec4<bool>"];case 22:return"i32";case 21:return"u32";default:throw new Error(`Unknown data type: ${e}`)}},yr=(e,r=1)=>{let a=Gu(e,r);return typeof a=="string"?a:a[0]},Or=(e,r=1)=>{let a=Gu(e,r);return typeof a=="string"?a:a[1]},Je=(...e)=>{let r=[];return e.forEach(a=>{a.length!==0&&r.push({type:12,data:a},{type:12,data:ge.computeStrides(a)})}),r},Yt=e=>e%4===0?4:e%2===0?2:1,up=(e="f32",r,a="0")=>!r||r===1?`${e}(${a})`:`vec${r}<${e}>(${a})`,fs=(e,r,a)=>e==="f32"?a:r===1?`f32(${a})`:`vec${r}<f32>(${a})`,an=(e,r)=>r===4?`(${e}.x + ${e}.y + ${e}.z + ${e}.w)`:r===2?`(${e}.x + ${e}.y)`:r===3?`(${e}.x + ${e}.y + ${e}.z)`:e,Qe=(e,r,a,s)=>e.startsWith("uniforms.")&&a>4?typeof r=="string"?s==="f16"?`${e}[(${r}) / 8][(${r}) % 8 / 4][(${r}) % 8 % 4]`:`${e}[(${r}) / 4][(${r}) % 4]`:s==="f16"?`${e}[${Math.floor(r/8)}][${Math.floor(r%8/4)}][${r%8%4}]`:`${e}[${Math.floor(r/4)}][${r%4}]`:a>1?`${e}[${r}]`:e,Ao=(e,r,a,s,o)=>{let p=typeof a=="number",d=p?a:a.length,g=[...new Array(d).keys()],m=d<2?"u32":d<=4?`vec${d}<u32>`:`array<u32, ${d}>`,_=Gu(r,o),v=typeof _=="string"?_:_[1],x=typeof _=="string"?_:_[0],T={indices:m,value:v,storage:x,tensor:r},C=Te=>typeof Te=="string"?Te:`${Te}u`,A={offsetToIndices:!1,indicesToOffset:!1,broadcastedIndicesToOffset:!1,set:!1,setByIndices:!1,get:!1,getByIndices:!1},R=p?"uniforms.":"",H=`${R}${e}_shape`,U=`${R}${e}_strides`,P="";for(let Te=0;Te<d-1;Te++)P+=`
    let dim${Te} = current / ${Qe(U,Te,d)};
    let rest${Te} = current % ${Qe(U,Te,d)};
    indices[${Te}] = dim${Te};
    current = rest${Te};
    `;P+=`indices[${d-1}] = current;`;let F=d<2?"":`
  fn o2i_${e}(offset: u32) -> ${T.indices} {
    var indices: ${T.indices};
    var current = offset;
    ${P}
    return indices;
  }`,G=Te=>(A.offsetToIndices=!0,d<2?Te:`o2i_${e}(${Te})`),K=[];if(d>=2)for(let Te=d-1;Te>=0;Te--)K.push(`${Qe(U,Te,d)} * (indices[${Te}])`);let ee=d<2?"":`
  fn i2o_${e}(indices: ${T.indices}) -> u32 {
    return ${K.join("+")};
  }`,ae=Te=>(A.indicesToOffset=!0,d<2?Te:`i2o_${e}(${Te})`),B=(...Te)=>d===0?"0u":`${T.indices}(${Te.map(C).join(",")})`,me=(Te,Be)=>d<2?`${Te}`:`${Qe(Te,Be,d)}`,_e=(Te,Be,We)=>d<2?`${Te}=${We};`:`${Qe(Te,Be,d)}=${We};`,Re={},Ue=(Te,Be)=>{A.broadcastedIndicesToOffset=!0;let We=`${Be.name}broadcastedIndicesTo${e}Offset`;if(We in Re)return`${We}(${Te})`;let Ie=[];for(let $t=d-1;$t>=0;$t--){let _r=Be.indicesGet("outputIndices",$t+Be.rank-d);Ie.push(`${me(U,$t)} * (${_r} % ${me(H,$t)})`)}return Re[We]=`fn ${We}(outputIndices: ${Be.type.indices}) -> u32 {
             return ${Ie.length>0?Ie.join("+"):"0u"};
           }`,`${We}(${Te})`},Me=(Te,Be)=>(()=>{if(T.storage===T.value)return`${e}[${Te}]=${Be};`;if(T.storage==="vec2<u32>"&&T.value==="i32")return`${e}[${Te}]=vec2<u32>(u32(${Be}), select(0u, 0xFFFFFFFFu, ${Be} < 0));`;if(T.storage==="vec2<u32>"&&T.value==="u32")return`${e}[${Te}]=vec2<u32>(u32(${Be}), 0u);`;if(T.storage==="u32"&&T.value==="vec4<bool>")return`${e}[${Te}]=dot(vec4<u32>(0x1, 0x100, 0x10000, 0x1000000), vec4<u32>(${Be}));`;throw new Error(`not supported combination of storage type ${T.storage} and value type ${T.value} yet`)})(),pe=Te=>(()=>{if(T.storage===T.value)return`${e}[${Te}]`;if(T.storage==="vec2<u32>"&&T.value==="i32")return`i32(${e}[${Te}].x)`;if(T.storage==="vec2<u32>"&&T.value==="u32")return`u32(${e}[${Te}].x)`;if(T.storage==="u32"&&T.value==="vec4<bool>")return`vec4<bool>(bool(${e}[${Te}] & 0xFFu), bool(${e}[${Te}] & 0xFF00u), bool(${e}[${Te}] & 0xFF0000u), bool(${e}[${Te}] & 0xFF000000u))`;throw new Error(`not supported combination of storage type ${T.storage} and value type ${T.value} yet`)})(),qe=d<2?"":`
  fn get_${e}ByIndices(indices: ${T.indices}) -> ${v} {
    return ${pe(`i2o_${e}(indices)`)};
  }`,Ve=d<2?"":(()=>{let Te=g.map(We=>`d${We}: u32`).join(", "),Be=g.map(We=>`d${We}`).join(", ");return`
  fn get_${e}(${Te}) -> ${v} {
    return get_${e}ByIndices(${B(Be)});
  }`})(),ze=(...Te)=>{if(Te.length!==d)throw new Error(`indices length must be ${d}`);let Be=Te.map(C).join(",");return d===0?pe("0u"):d===1?pe(Be[0]):(A.get=!0,A.getByIndices=!0,A.indicesToOffset=!0,`get_${e}(${Be})`)},ht=Te=>d<2?pe(Te):(A.getByIndices=!0,A.indicesToOffset=!0,`get_${e}ByIndices(${Te})`),Ce=d<2?"":`
  fn set_${e}ByIndices(indices: ${T.indices}, value: ${v}) {
    ${Me(`i2o_${e}(indices)`,"value")}
  }`,nt=d<2?"":(()=>{let Te=g.map(We=>`d${We}: u32`).join(", "),Be=g.map(We=>`d${We}`).join(", ");return`
  fn set_${e}(${Te}, value: ${v}) {
    set_${e}ByIndices(${B(Be)}, value);
  }`})();return{impl:()=>{let Te=[],Be=!1;return A.offsetToIndices&&(Te.push(F),Be=!0),A.indicesToOffset&&(Te.push(ee),Be=!0),A.broadcastedIndicesToOffset&&(Object.values(Re).forEach(We=>Te.push(We)),Be=!0),A.set&&(Te.push(nt),Be=!0),A.setByIndices&&(Te.push(Ce),Be=!0),A.get&&(Te.push(Ve),Be=!0),A.getByIndices&&(Te.push(qe),Be=!0),!p&&Be&&Te.unshift(`const ${H} = ${T.indices}(${a.join(",")});`,`const ${U} = ${T.indices}(${ge.computeStrides(a).join(",")});`),Te.join(`
`)},type:T,offsetToIndices:G,indicesToOffset:ae,broadcastedIndicesToOffset:Ue,indices:B,indicesGet:me,indicesSet:_e,set:(...Te)=>{if(Te.length!==d+1)throw new Error(`indices length must be ${d}`);let Be=Te[d];if(typeof Be!="string")throw new Error("value must be string");let We=Te.slice(0,d).map(C).join(",");return d===0?Me("0u",Be):d===1?Me(We[0],Be):(A.set=!0,A.setByIndices=!0,A.indicesToOffset=!0,`set_${e}(${We}, ${Be})`)},setByOffset:Me,setByIndices:(Te,Be)=>d<2?Me(Te,Be):(A.setByIndices=!0,A.indicesToOffset=!0,`set_${e}ByIndices(${Te}, ${Be});`),get:ze,getByOffset:pe,getByIndices:ht,usage:s,name:e,strides:U,shape:H,rank:d}},$e=(e,r,a,s=1)=>Ao(e,r,a,"input",s),je=(e,r,a,s=1)=>Ao(e,r,a,"output",s),T0=(e,r,a)=>Ao(e,r,a,"atomicOutput",1),Bp=(e,r,a,s=1)=>Ao(e,r,a,"internal",s),Ag=class{constructor(e,r){this.normalizedDispatchGroup=e,this.limits=r,this.internalVariables=[],this.variables=[],this.uniforms=[],this.variableIndex=0}guardAgainstOutOfBoundsWorkgroupSizes(e){return`if (global_idx >= ${typeof e=="number"?`${e}u`:e}) { return; }`}mainStart(e=gs){let r=typeof e=="number"?e:e[0],a=typeof e=="number"?1:e[1],s=typeof e=="number"?1:e[2];if(r>this.limits.maxComputeWorkgroupSizeX||a>this.limits.maxComputeWorkgroupSizeY||s>this.limits.maxComputeWorkgroupSizeZ)throw new Error(`workgroup size [${r}, ${a}, ${s}] exceeds the maximum workgroup size [${this.limits.maxComputeWorkgroupSizeX}, ${this.limits.maxComputeWorkgroupSizeY}, ${this.limits.maxComputeWorkgroupSizeZ}].`);if(r*a*s>this.limits.maxComputeInvocationsPerWorkgroup)throw new Error(`workgroup size [${r}, ${a}, ${s}] exceeds the maximum workgroup invocations ${this.limits.maxComputeInvocationsPerWorkgroup}.`);let o=this.normalizedDispatchGroup[1]===1&&this.normalizedDispatchGroup[2]===1,p=o?`@builtin(global_invocation_id) global_id : vec3<u32>,
    @builtin(workgroup_id) workgroup_id : vec3<u32>,
    @builtin(local_invocation_index) local_idx : u32,
    @builtin(local_invocation_id) local_id : vec3<u32>`:`@builtin(global_invocation_id) global_id : vec3<u32>,
                                             @builtin(local_invocation_id) local_id : vec3<u32>,
    @builtin(local_invocation_index) local_idx : u32,
    @builtin(workgroup_id) workgroup_id : vec3<u32>,
    @builtin(num_workgroups) num_workgroups : vec3<u32>`,d=o?`let global_idx = global_id.x;
         let workgroup_index = workgroup_id.x;`:`let workgroup_index = workgroup_id.z * num_workgroups[0] * num_workgroups[1] +
             workgroup_id.y * num_workgroups[0] + workgroup_id.x;
         let global_idx = workgroup_index * ${r*a*s}u + local_idx;`;return`@compute @workgroup_size(${r}, ${a}, ${s})
  fn main(${p}) {
    ${d}
  `}appendVariableUniforms(e){e.rank!==0&&(e.shape.startsWith("uniforms.")&&this.uniforms.push({name:e.shape.replace("uniforms.",""),type:"u32",length:e.rank}),e.strides.startsWith("uniforms.")&&this.uniforms.push({name:e.strides.replace("uniforms.",""),type:"u32",length:e.rank}))}declareVariable(e,r){if(e.usage==="internal")throw new Error("cannot use internal variable with declareVariable(). use registerInternalVariables() instead.");this.variables.push(e),this.appendVariableUniforms(e);let a=e.usage==="input"?"read":"read_write",s=e.usage==="atomicOutput"?"atomic<i32>":e.type.storage;return`@group(0) @binding(${r}) var<storage, ${a}> ${e.name}: array<${s}>;`}declareVariables(...e){return e.map(r=>this.declareVariable(r,this.variableIndex++)).join(`
`)}registerInternalVariable(e){if(e.usage!=="internal")throw new Error("cannot use input or output variable with registerInternalVariable(). use declareVariables() instead.");this.internalVariables.push(e),this.appendVariableUniforms(e)}registerInternalVariables(...e){return e.forEach(r=>this.registerInternalVariable(r)),this}registerUniform(e,r,a=1){return this.uniforms.push({name:e,type:r,length:a}),this}registerUniforms(e){return this.uniforms=this.uniforms.concat(e),this}uniformDeclaration(){if(this.uniforms.length===0)return"";let e=[];for(let{name:r,type:a,length:s}of this.uniforms)if(s&&s>4)a==="f16"?e.push(`@align(16) ${r}:array<mat2x4<${a}>, ${Math.ceil(s/8)}>`):e.push(`${r}:array<vec4<${a}>, ${Math.ceil(s/4)}>`);else{let o=s==null||s===1?a:`vec${s}<${a}>`;e.push(`${r}:${o}`)}return`
      struct Uniforms { ${e.join(", ")} };
      @group(0) @binding(${this.variableIndex}) var<uniform> uniforms: Uniforms;`}get additionalImplementations(){return this.uniformDeclaration()+this.variables.map(e=>e.impl()).join(`
`)+this.internalVariables.map(e=>e.impl()).join(`
`)}get variablesInfo(){if(this.uniforms.length===0)return;let e=r=>[12,10,1,6][["u32","f16","f32","i32"].indexOf(r)];return this.uniforms.map(r=>[e(r.type),r.length??1])}},k0=(e,r)=>new Ag(e,r)}),Og,Ed,Rg,Bg,Mg,Dg,si,E0,I0,nn=Ee(()=>{ut(),ct(),Jt(),ft(),Og=(e,r)=>{if(!e||e.length!==1)throw new Error("Transpose requires 1 input.");if(r.length!==0&&r.length!==e[0].dims.length)throw new Error(`perm size ${r.length} does not match input rank ${e[0].dims.length}`)},Ed=(e,r)=>r.length!==0?r:[...new Array(e).keys()].reverse(),Rg=(e,r)=>ge.sortBasedOnPerm(e,Ed(e.length,r)),Bg=(e,r,a,s)=>{let o=`fn perm(i: ${s.type.indices}) -> ${a.type.indices} {
    var a: ${a.type.indices};`;for(let p=0;p<r;++p)o+=`a[${e[p]}]=i[${p}];`;return o+="return a;}"},Mg=(e,r)=>{let a=[],s=[];for(let o=0;o<e.length;++o)e[o]!==1&&a.push(e[o]),e[r[o]]!==1&&s.push(r[o]);return{newShape:a,newPerm:s}},Dg=(e,r)=>{let a=0;for(let s=0;s<e.length;++s)if(r[e[s]]!==1){if(e[s]<a)return!1;a=e[s]}return!0},si=(e,r)=>{let a=e.dataType,s=e.dims.length,o=Ed(s,r),p=Rg(e.dims,o),d=e.dims,g=p,m=s<2||Dg(o,e.dims),_;if(m)return _=A=>{let R=$e("input",a,d,4),H=je("output",a,g,4);return`
  ${A.registerUniform("output_size","u32").declareVariables(R,H)}
  ${A.mainStart()}
    ${A.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    output[global_idx] = input[global_idx];
  }`},{name:"TransposeCopy",shaderCache:{inputDependencies:["type"]},getRunData:()=>{let A=ge.size(p);return{outputs:[{dims:p,dataType:e.dataType}],dispatchGroup:{x:Math.ceil(A/64/4)},programUniforms:[{type:12,data:Math.ceil(A/4)}]}},getShaderSource:_};let{newShape:v,newPerm:x}=Mg(e.dims,o),T=ge.areEqual(x,[2,3,1]),C=ge.areEqual(x,[3,1,2]);if(v.length===2||T||C){d=T?[v[0],v[1]*v[2]]:C?[v[0]*v[1],v[2]]:v,g=[d[1],d[0]];let A=16;return _=R=>{let H=$e("a",a,d.length),U=je("output",a,g.length);return`
  ${R.registerUniform("output_size","u32").declareVariables(H,U)}
  var<workgroup> tile : array<array<${U.type.value}, ${A+1}>, ${A}>;
  ${R.mainStart([A,A,1])}
    let stride = (uniforms.output_shape[1] - 1) / ${A} + 1;
    let workgroup_id_x = workgroup_index % stride;
    let workgroup_id_y = workgroup_index / stride;
    let input_col = workgroup_id_y * ${A}u + local_id.x;
    let input_row = workgroup_id_x * ${A}u + local_id.y;
    if (input_row < uniforms.a_shape[0] && input_col < uniforms.a_shape[1]) {
      tile[local_id.y][local_id.x] = ${H.getByIndices(`${H.type.indices}(input_row, input_col)`)};
    }
    workgroupBarrier();

    let output_col = workgroup_id_x * ${A}u + local_id.x;
    let output_row = workgroup_id_y * ${A}u + local_id.y;
    if (output_row < uniforms.output_shape[0] && output_col < uniforms.output_shape[1]) {
      ${U.setByIndices(`${U.type.indices}(output_row, output_col)`,"tile[local_id.x][local_id.y]")}
    }
  }`},{name:"TransposeShared",shaderCache:{inputDependencies:["type"]},getRunData:()=>{let R=ge.size(p);return{outputs:[{dims:p,dataType:e.dataType}],dispatchGroup:{x:Math.ceil(g[1]/A),y:Math.ceil(g[0]/A)},programUniforms:[{type:12,data:R},...Je(d,g)]}},getShaderSource:_}}return _=A=>{let R=$e("a",a,d.length),H=je("output",a,g.length);return`
  ${A.registerUniform("output_size","u32").declareVariables(R,H)}

  ${Bg(o,s,R,H)}

  ${A.mainStart()}
    ${A.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let indices = ${H.offsetToIndices("global_idx")};
    let aIndices = perm(indices);

    ${H.setByOffset("global_idx",R.getByIndices("aIndices"))}
  }`},{name:"Transpose",shaderCache:{hint:`${r}`,inputDependencies:["rank"]},getRunData:()=>{let A=ge.size(p);return{outputs:[{dims:p,dataType:e.dataType}],dispatchGroup:{x:Math.ceil(A/64)},programUniforms:[{type:12,data:A},...Je(d,g)]}},getShaderSource:_}},E0=(e,r)=>{Og(e.inputs,r.perm),e.compute(si(e.inputs[0],r.perm))},I0=e=>Nt({perm:e.perm})}),Ng,Pg,Ug,Lg,qg,Vg,Wg,Gg,Fg,Hg,Oi,z0,C0,A0,O0,R0,B0,M0,D0,N0,P0,e1=Ee(()=>{ut(),ct(),ft(),Mp(),nn(),Ng={max:"select(bestValue, candidate, candidate > bestValue)",min:"select(bestValue, candidate, candidate < bestValue)",mean:"bestValue + candidate",sum:"bestValue + candidate",prod:"bestValue * candidate",sumSquare:"bestValue + candidate * candidate",logSumExp:"bestValue + exp(candidate)",l1:"bestValue + abs(candidate)",l2:"bestValue + candidate * candidate",logSum:"bestValue + candidate"},Pg={max:"select(bestValue, candidate, candidate > bestValue)",min:"select(bestValue, candidate, candidate < bestValue)",mean:"bestValue + candidate",sum:"bestValue + candidate",prod:"bestValue * candidate",sumSquare:"bestValue + candidate",logSumExp:"bestValue + candidate",l1:"bestValue + candidate",l2:"bestValue + candidate",logSum:"bestValue + candidate"},Ug={max:"_A[offset]",min:"_A[offset]",mean:"0",sum:"0",prod:"1",sumSquare:"0",logSumExp:"0",l1:"0",l2:"0",logSum:"0"},Lg={max:"bestValue",min:"bestValue",sum:"bestValue",prod:"bestValue",sumSquare:"bestValue",logSumExp:"log(bestValue)",l1:"bestValue",l2:"sqrt(bestValue)",logSum:"log(bestValue)"},qg=(e,r)=>{let a=[];for(let s=r-e;s<r;++s)a.push(s);return a},Vg=(e,r)=>{let a=[],s=e.length;for(let p=0;p<s;p++)r.indexOf(p)===-1&&a.push(e[p]);let o=r.map(p=>e[p]);return[a,o]},Wg=(e,r)=>{let a=e.length+r.length,s=[],o=0;for(let p=0;p<a;p++)r.indexOf(p)===-1?s.push(e[o++]):s.push(1);return s},Gg=(e,r)=>{for(let a=0;a<e.length;++a)if(e[e.length-a-1]!==r-1-a)return!1;return!0},Fg=(e,r)=>{let a=[];if(!Gg(e,r)){for(let s=0;s<r;++s)e.indexOf(s)===-1&&a.push(s);e.forEach(s=>a.push(s))}return a},Hg=(e,r,a,s,o,p,d)=>{let g=a[0].dims,m=ge.size(p),_=ge.size(d),v=$e("_A",a[0].dataType,g),x=je("output",o,p),T=64;m===1&&(T=256);let C=`
          var<workgroup> aBestValues : array<f32, ${T}>;
       `,A=R=>`
        ${R.registerUniform("reduceSize","u32").declareVariables(v,x)}
        ${C}
        fn DIV_CEIL(a : u32, b : u32) -> u32 {
          return ((a - 1u) / b + 1u);
         }
         ${R.mainStart(T)}

          let outputIndex = global_idx / ${T};
          let offset = outputIndex * uniforms.reduceSize;

          var bestValue = f32(${Ug[s]});
          let Length = uniforms.reduceSize;
          for (var k = local_idx; k < Length; k = k + ${T}) {
           let candidate = f32(${v.getByOffset("offset + k")});
           bestValue = ${Ng[s]};
          }
          aBestValues[local_idx] = bestValue;
          workgroupBarrier();

         var reduceSize = min(Length, ${T}u);
         for (var currentSize = reduceSize / 2u; reduceSize > 1u;
             currentSize = reduceSize / 2u) {
           let interval = DIV_CEIL(reduceSize, 2u);
           if (local_idx < currentSize) {
            let candidate = aBestValues[local_idx + interval];
            bestValue = ${Pg[s]};
            aBestValues[local_idx] = bestValue;
           }
           reduceSize = interval;
           workgroupBarrier();
         }

         if (local_idx == 0u) {
          ${x.setByOffset("outputIndex",`${s==="mean"?`${x.type.storage}(bestValue / f32(uniforms.reduceSize))`:`${x.type.storage}(${Lg[s]})`}`)};
         }
        }`;return{name:e,shaderCache:{hint:`${r};${T}`,inputDependencies:["type"]},getShaderSource:A,getRunData:()=>({outputs:[{dims:p,dataType:o}],dispatchGroup:{x:m},programUniforms:[{type:12,data:_}]})}},Oi=(e,r,a,s)=>{let o=e.inputs.length===1?a:lp(e.inputs,a),p=o.axes;p.length===0&&!o.noopWithEmptyAxes&&(p=e.inputs[0].dims.map((C,A)=>A));let d=ge.normalizeAxes(p,e.inputs[0].dims.length),g=d,m=e.inputs[0],_=Fg(g,e.inputs[0].dims.length);_.length>0&&(m=e.compute(si(e.inputs[0],_),{inputs:[0],outputs:[-1]})[0],g=qg(g.length,m.dims.length));let[v,x]=Vg(m.dims,g),T=v;o.keepDims&&(T=Wg(v,d)),e.compute(Hg(r,o.cacheKey,[m],s,e.inputs[0].dataType,T,x),{inputs:[m]})},z0=(e,r)=>{Oi(e,"ReduceMeanShared",r,"mean")},C0=(e,r)=>{Oi(e,"ReduceL1Shared",r,"l1")},A0=(e,r)=>{Oi(e,"ReduceL2Shared",r,"l2")},O0=(e,r)=>{Oi(e,"ReduceLogSumExpShared",r,"logSumExp")},R0=(e,r)=>{Oi(e,"ReduceMaxShared",r,"max")},B0=(e,r)=>{Oi(e,"ReduceMinShared",r,"min")},M0=(e,r)=>{Oi(e,"ReduceProdShared",r,"prod")},D0=(e,r)=>{Oi(e,"ReduceSumShared",r,"sum")},N0=(e,r)=>{Oi(e,"ReduceSumSquareShared",r,"sumSquare")},P0=(e,r)=>{Oi(e,"ReduceLogSumShared",r,"logSum")}}),Ri,jg,nl,lp,Bi,Kg,Zg,Qg,Xg,Yg,Jg,ey,ty,ry,iy,Mi,U0,L0,q0,V0,W0,G0,F0,H0,j0,K0,Mp=Ee(()=>{ut(),ct(),Jt(),ft(),e1(),Ri=e=>{if(!e||e.length===0||e.length>2)throw new Error("Reduce op requires 1 or 2 inputs.");if(e.length===2&&e[1].dims.length!==1)throw new Error("Invalid axes input dims.")},jg=e=>["","",`var value = ${e.getByIndices("input_indices")};`,""],nl=(e,r,a,s,o,p,d=!1,g=!1)=>{let m=[],_=a[0].dims,v=_.length,x=ge.normalizeAxes(o,v),T=!g&&x.length===0;_.forEach((R,H)=>{T||x.indexOf(H)>=0?d&&m.push(1):m.push(R)});let C=m.length,A=ge.size(m);return{name:e,shaderCache:r,getShaderSource:R=>{let H=[],U=$e("_A",a[0].dataType,v),P=je("output",p,C),F=s(U,P,x),G=F[2];for(let K=0,ee=0;K<v;K++)T||x.indexOf(K)>=0?(d&&ee++,G=`for(var j${K}: u32 = 0; j${K} < ${_[K]}; j${K}++) {
                  ${F[2].includes("last_index")?`let last_index = j${K};`:""}
                  ${U.indicesSet("input_indices",K,`j${K}`)}
                  ${G}
                }`):(H.push(`${U.indicesSet("input_indices",K,P.indicesGet("output_indices",ee))};`),ee++);return`

        ${R.registerUniform("output_size","u32").declareVariables(U,P)}

        ${R.mainStart()}
          ${R.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
          var input_indices: ${U.type.indices};
          let output_indices = ${P.offsetToIndices("global_idx")};

          ${H.join(`
`)}
          ${F[0]}       // init ops for reduce max/min
          ${F[1]}
          ${G}
          ${F[3]}
          ${F.length===4?P.setByOffset("global_idx","value"):F.slice(4).join(`
`)}
        }`},getRunData:()=>({outputs:[{dims:m,dataType:p}],dispatchGroup:{x:Math.ceil(A/64)},programUniforms:[{type:12,data:A},...Je(_,m)]})}},lp=(e,r)=>{let a=[];return e[1].dims[0]>0&&e[1].getBigInt64Array().forEach(s=>a.push(Number(s))),Nt({axes:a,keepDims:r.keepDims,noopWithEmptyAxes:r.noopWithEmptyAxes})},Bi=(e,r,a,s)=>{let o=e.inputs,p=o.length===1?a:lp(o,a);e.compute(nl(r,{hint:p.cacheKey,inputDependencies:["rank"]},[o[0]],p.noopWithEmptyAxes&&p.axes.length===0?jg:s,p.axes,o[0].dataType,p.keepDims,p.noopWithEmptyAxes),{inputs:[0]})},Kg=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceLogSum",r,(a,s)=>[`var value = ${s.type.storage}(0);`,"",`value += ${a.getByIndices("input_indices")};`,"value = log(value);"])},Zg=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceL1",r,(a,s)=>[`var value = ${s.type.storage}(0);`,"",`value += abs(${a.getByIndices("input_indices")});`,""])},Qg=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceL2",r,(a,s)=>[`var t = ${s.type.value}(0); var value = ${s.type.value}(0);`,"",`t = ${a.getByIndices("input_indices")}; value += (t * t);`,"value = sqrt(value);"])},Xg=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceLogSumExp",r,(a,s)=>[`var value = ${s.type.storage}(0);`,"",`value += exp(${a.getByIndices("input_indices")});`,"value = log(value);"])},Yg=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceMax",r,(a,s,o)=>{let p=[];for(let d=0;d<a.rank;d++)(o.indexOf(d)>=0||o.length===0)&&p.push(a.indicesSet("input_indices",d,0));return[`${p.join(`
`)}`,`var value = ${a.getByIndices("input_indices")};`,`value = max(value, ${a.getByIndices("input_indices")});`,""]})},Jg=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceMean",r,(a,s,o)=>{let p=1;for(let d=0;d<a.rank;d++)(o.indexOf(d)>=0||o.length===0)&&(p*=e.inputs[0].dims[d]);return["var sum = f32(0);","",`sum += f32(${a.getByIndices("input_indices")});`,`let value = ${s.type.value}(sum / ${p});`]})},ey=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceMin",r,(a,s,o)=>{let p=[];for(let d=0;d<a.rank;d++)(o.indexOf(d)>=0||o.length===0)&&p.push(`input_indices[${d}] = 0;`);return[`${p.join(`
`)}`,`var value = ${a.getByIndices("input_indices")};`,`value = min(value, ${a.getByIndices("input_indices")});`,""]})},ty=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceProd",r,(a,s)=>[`var value = ${s.type.storage}(1);`,"",`value *= ${a.getByIndices("input_indices")};`,""])},ry=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceSum",r,(a,s)=>[`var value = ${s.type.storage}(0);`,"",`value += ${a.getByIndices("input_indices")};`,""])},iy=(e,r)=>{Ri(e.inputs),Bi(e,"ReduceSumSquare",r,(a,s)=>[`var t = ${s.type.value}(0); var value = ${s.type.value}(0);`,"",`t = ${a.getByIndices("input_indices")}; value += t * t;`,""])},Mi=(e,r,a)=>{if(r.length===0)return a;let s=1,o=1;for(let p=0;p<r.length;p++)r.indexOf(p)===-1?s*=e[p]:o*=e[p];return o<32&&s>1024},U0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?Jg(e,r):z0(e,r)},L0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?Zg(e,r):C0(e,r)},q0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?Qg(e,r):A0(e,r)},V0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?Xg(e,r):O0(e,r)},W0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?Yg(e,r):R0(e,r)},G0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?ey(e,r):B0(e,r)},F0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?ty(e,r):M0(e,r)},H0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?ry(e,r):D0(e,r)},j0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?iy(e,r):N0(e,r)},K0=(e,r)=>{Mi(e.inputs[0].dims,r.axes,r.noopWithEmptyAxes)?Kg(e,r):P0(e,r)}}),Id,Z0,Q0,dp,t1=Ee(()=>{ut(),Jt(),Mp(),Id=e=>{if(!e||e.length===0||e.length>2)throw new Error("ArgMinMaxOp op requires 1 or 2 inputs.");if(e[0].dataType!==1)throw new Error("Invalid input type.")},Z0=(e,r)=>{Id(e.inputs);let a=(s,o,p)=>{let d=[];for(let g=0;g<s.rank;g++)(p.indexOf(g)>=0||p.length===0)&&d.push(`input_indices[${g}] = 0;`);return[`${d.join(`
`)}`,`var value = ${s.getByIndices("input_indices")};
var best_index : i32 = 0;`,`if (${s.getByIndices("input_indices")} ${r.selectLastIndex>0?"<=":"<"} value) {
         value = ${s.getByIndices("input_indices")};
         best_index = i32(last_index);
       }`,"",o.setByOffset("global_idx","best_index")]};e.compute(nl("ArgMin",{hint:r.cacheKey,inputDependencies:["rank"]},[e.inputs[0]],a,[r.axis],7,r.keepDims),{inputs:[0]})},Q0=(e,r)=>{Id(e.inputs);let a=(s,o,p)=>{let d=[];for(let g=0;g<s.rank;g++)(p.indexOf(g)>=0||p.length===0)&&d.push(`input_indices[${g}] = 0;`);return[`${d.join(`
`)}`,`var value = ${s.getByIndices("input_indices")};
var best_index : i32 = 0;`,`if (${s.getByIndices("input_indices")} ${r.selectLastIndex>0?">=":">"} value) {
         value = ${s.getByIndices("input_indices")};
         best_index = i32(last_index);
       }`,"",o.setByOffset("global_idx","best_index")]};e.compute(nl("argMax",{hint:r.cacheKey,inputDependencies:["rank"]},[e.inputs[0]],a,[r.axis],7,r.keepDims),{inputs:[0]})},dp=e=>Nt(e)}),ay,Fu,ny,sy,oy,Fo,uy,X0,Dp=Ee(()=>{ut(),ct(),Rp(),ft(),ay=(e,r)=>{let a=e[0],s=e[1],o=e[2],p=e[3],d=e[4],g=e[5];if(d&&g)throw new Error("Attention cannot have both past and attention_bias");if(a.dims.length!==3)throw new Error('Input "input" must have 3 dimensions');let m=a.dims[0],_=a.dims[1],v=a.dims[2];if(o.dims.length!==1)throw new Error('Input "bias" is expected to have 1 dimensions');if(s.dims.length!==2)throw new Error('Input "weights" is expected to have 2 dimensions');if(s.dims[0]!==v)throw new Error("Input 1 dimension 0 should have same length as dimension 2 of input 0");if(o.dims[0]!==s.dims[1])throw new Error('Input "bias" dimension 0 should have same length as dimension 1 of input "weights"');let x=o.dims[0]/3,T=x,C=T;if(r.qkvHiddenSizes.length>0){if(r.qkvHiddenSizes.length!==3)throw new Error("qkv_hidden_sizes attribute should have 3 elements");for(let F of r.qkvHiddenSizes)if(F%r.numHeads!==0)throw new Error("qkv_hidden_sizes should be divisible by num_heads");x=r.qkvHiddenSizes[0],T=r.qkvHiddenSizes[1],C=r.qkvHiddenSizes[2]}let A=_;if(x!==T)throw new Error("qkv_hidden_sizes first element should be same as the second");if(o.dims[0]!==x+T+C)throw new Error('Input "bias" dimension 0 should have same length as sum of Q/K/V hidden sizes');let R=0;if(d){if(T!==C)throw new Error('Input "past" expect k_hidden_size == v_hidden_size');if(d.dims.length!==5)throw new Error('Input "past" must have 5 dimensions');if(d.dims[0]!==2)throw new Error('Input "past" first dimension must be 2');if(d.dims[1]!==m)throw new Error('Input "past" second dimension must be batch_size');if(d.dims[2]!==r.numHeads)throw new Error('Input "past" third dimension must be num_heads');if(d.dims[4]!==T/r.numHeads)throw new Error('Input "past" fifth dimension must be k_hidden_size / num_heads');r.pastPresentShareBuffer||(R=d.dims[3])}let H=A+R,U=-1,P=0;if(p)throw new Error("Mask not supported");if(d)throw new Error("past is not supported");if(g){if(g.dims.length!==4)throw new Error('Input "attention_bias" must have 4 dimensions');if(g.dims[0]!==m||g.dims[1]!==r.numHeads||g.dims[2]!==_||g.dims[3]!==H)throw new Error('Expect "attention_bias" shape (batch_size, num_heads, sequence_length, total_sequence_length)')}return{batchSize:m,sequenceLength:_,pastSequenceLength:R,kvSequenceLength:A,totalSequenceLength:H,maxSequenceLength:U,inputHiddenSize:v,hiddenSize:x,vHiddenSize:C,headSize:Math.floor(x/r.numHeads),vHeadSize:Math.floor(C/r.numHeads),numHeads:r.numHeads,isUnidirectional:!1,pastPresentShareBuffer:!1,maskFilterValue:r.maskFilterValue,maskType:P,scale:r.scale,broadcastResPosBias:!1,passPastInKv:!1,qkvFormat:1}},Fu=(e,r,a)=>r&&e?`
      let total_sequence_length_input = u32(${r.getByOffset("0")});
      let present_sequence_length = max(total_sequence_length_input, uniforms.past_sequence_length);
      let is_subsequent_prompt: bool = sequence_length > 1 && sequence_length != total_sequence_length_input;
      let is_first_prompt: bool = is_subsequent_prompt == false && sequence_length == total_sequence_length_input;
      total_sequence_length = u32(${e==null?void 0:e.getByOffset("batchIdx")}) + 1;
      var past_sequence_length: u32 = 0;
      if (is_first_prompt == false) {
        past_sequence_length = total_sequence_length - sequence_length;
      }
       `:`
    ${a?"let past_sequence_length = uniforms.past_sequence_length":""};
    let present_sequence_length = total_sequence_length;
    `,ny=(e,r,a,s,o,p,d,g)=>{let m=Yt(d?1:p),_=64,v=p/m;v<_&&(_=32);let x=Math.ceil(p/m/_),T=[{type:12,data:r},{type:12,data:a},{type:12,data:s},{type:12,data:o},{type:12,data:v},{type:12,data:x}],C=yr(e.dataType,m),A=Or(1,m),R=["type"];d&&R.push("type"),g&&R.push("type");let H=U=>{let P=je("x",e.dataType,e.dims,m),F=[P],G=d?$e("seq_lens",d.dataType,d.dims):void 0;G&&F.push(G);let K=g?$e("total_sequence_length_input",g.dataType,g.dims):void 0;K&&F.push(K);let ee=Or(e.dataType),ae=[{name:"batch_size",type:"u32"},{name:"num_heads",type:"u32"},{name:"past_sequence_length",type:"u32"},{name:"sequence_length",type:"u32"},{name:"total_sequence_length",type:"u32"},{name:"elements_per_thread",type:"u32"}];return`
  var<workgroup> thread_max: array<f32, ${_}>;
  var<workgroup> thread_sum: array<f32, ${_}>;
  ${U.registerUniforms(ae).declareVariables(...F)}
  ${U.mainStart([_,1,1])}
    let batchIdx = workgroup_id.z / uniforms.num_heads;
    let headIdx = workgroup_id.z % uniforms.num_heads;
    let sequence_length = uniforms.sequence_length;
    var total_sequence_length = uniforms.total_sequence_length;
    ${Fu(G,K,!1)}
    let local_offset = local_idx * uniforms.elements_per_thread;
    let offset = (global_idx / ${_}) * uniforms.total_sequence_length + local_offset;
    let seq_causal_length = ${d?"u32(past_sequence_length + workgroup_id.y + 1)":"total_sequence_length"};
    var thread_max_vector = ${A}(-3.4028234663852886e+38f);
    for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
      thread_max_vector = max(${A}(x[offset + i]), thread_max_vector);
    }
    thread_max[local_idx] = ${(()=>{switch(m){case 1:return"thread_max_vector";case 2:return"max(thread_max_vector.x, thread_max_vector.y)";case 4:return"max(max(thread_max_vector.x, thread_max_vector.y), max(thread_max_vector.z, thread_max_vector.w))";default:throw new Error(`Unsupported components: ${m}`)}})()};
    workgroupBarrier();

    var max_value =  f32(-3.4028234663852886e+38f);
    for (var i = 0u; i < ${_}; i++) {
      max_value = max(thread_max[i], max_value);
    }

    var sum_vector = ${A}(0);
    for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
      sum_vector += exp(${A}(x[offset + i]) - max_value);
    }
    thread_sum[local_idx] = ${(()=>{switch(m){case 1:return"sum_vector";case 2:return"sum_vector.x + sum_vector.y";case 4:return"sum_vector.x + sum_vector.y + sum_vector.z + sum_vector.w";default:throw new Error(`Unsupported components: ${m}`)}})()};
    workgroupBarrier();

    var sum: f32 = 0;
    for (var i = 0u; i < ${_}; i++) {
      sum += thread_sum[i];
    }

    if (sum == 0) {
      for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
        x[offset + i] = ${P.type.value}(${ee}(1.0) / ${ee}(seq_causal_length));
      }
    } else {
      for (var i: u32 = 0; i < uniforms.elements_per_thread && i + local_offset < seq_causal_length; i++) {
        var f32input = ${A}(x[offset + i]);
        x[offset + i] = ${P.type.value}(exp(f32input - max_value) / sum);
      }
    }
      ${d?`
        for (var total_seq_id: u32 = seq_causal_length; total_seq_id + local_offset < uniforms.total_sequence_length; total_seq_id++) {
          x[offset + total_seq_id] = ${P.type.value}(${ee}(0));
        }`:""};
  }`};return{name:"AttentionProbsSoftmax",shaderCache:{hint:`${_};${C};${m}`,inputDependencies:R},getShaderSource:H,getRunData:()=>({outputs:[],dispatchGroup:{x:1,y:o,z:r*a},programUniforms:T})}},sy=(e,r,a,s,o,p,d,g,m)=>{let _=d+p.kvSequenceLength,v=[p.batchSize,p.numHeads,p.sequenceLength,_],x=e>1&&s,T=p.kvNumHeads?p.kvNumHeads:p.numHeads,C=x?[p.batchSize,T,_,p.headSize]:void 0,A=p.nReps?p.nReps:1,R=p.scale===0?1/Math.sqrt(p.headSize):p.scale,H=Yt(p.headSize),U=p.headSize/H,P=12,F={x:Math.ceil(_/P),y:Math.ceil(p.sequenceLength/P),z:p.batchSize*p.numHeads},G=[{type:12,data:p.sequenceLength},{type:12,data:U},{type:12,data:_},{type:12,data:p.numHeads},{type:12,data:p.headSize},{type:1,data:R},{type:12,data:d},{type:12,data:p.kvSequenceLength},{type:12,data:A}],K=x&&s&&ge.size(s.dims)>0,ee=["type","type"];K&&ee.push("type"),o&&ee.push("type"),g&&ee.push("type"),m&&ee.push("type");let ae=[{dims:v,dataType:r.dataType,gpuDataType:0}];x&&ae.push({dims:C,dataType:r.dataType,gpuDataType:0});let B=me=>{let _e=$e("q",r.dataType,r.dims,H),Re=$e("key",a.dataType,a.dims,H),Ue=[_e,Re];if(K){let Ce=$e("past_key",s.dataType,s.dims,H);Ue.push(Ce)}o&&Ue.push($e("attention_bias",o.dataType,o.dims));let Me=g?$e("seq_lens",g.dataType,g.dims):void 0;Me&&Ue.push(Me);let pe=m?$e("total_sequence_length_input",m.dataType,m.dims):void 0;pe&&Ue.push(pe);let qe=je("output",r.dataType,v),Ve=[qe];x&&Ve.push(je("present_key",r.dataType,C,H));let ze=Or(1,H),ht=[{name:"M",type:"u32"},{name:"K",type:"u32"},{name:"N",type:"u32"},{name:"num_heads",type:"u32"},{name:"head_size",type:"u32"},{name:"alpha",type:"f32"},{name:"past_sequence_length",type:"u32"},{name:"kv_sequence_length",type:"u32"},{name:"n_reps",type:"u32"}];return`
  const TILE_SIZE = ${P}u;

  var<workgroup> tileQ: array<${_e.type.storage}, ${P*P}>;
  var<workgroup> tileK: array<${_e.type.storage}, ${P*P}>;
  ${me.registerUniforms(ht).declareVariables(...Ue,...Ve)}
  ${me.mainStart([P,P,1])}
    // x holds the N and y holds the M
    let headIdx = workgroup_id.z % uniforms.num_heads;
    let kvHeadIdx = ${A===1?"headIdx":"headIdx / uniforms.n_reps"};
    let kv_num_heads = ${A===1?"uniforms.num_heads":"uniforms.num_heads / uniforms.n_reps"};
    let batchIdx = workgroup_id.z / uniforms.num_heads;
    let m = workgroup_id.y * TILE_SIZE;
    let n = workgroup_id.x * TILE_SIZE;
    let sequence_length = uniforms.M;
    var total_sequence_length = uniforms.N;
    ${Fu(Me,pe,!0)}
    let absKvHeadIdx = batchIdx * kv_num_heads + kvHeadIdx;
    let qOffset = workgroup_id.z * uniforms.M * uniforms.K + m * uniforms.K;
    ${K&&x?"let pastKeyOffset = absKvHeadIdx * uniforms.past_sequence_length * uniforms.K;":""};
    let kOffset = absKvHeadIdx * uniforms.kv_sequence_length * uniforms.K;
    ${x?"let presentKeyOffset = absKvHeadIdx * uniforms.N * uniforms.K;":""}
    var value = ${ze}(0);
    for (var w: u32 = 0u; w < uniforms.K; w += TILE_SIZE) {
      if (global_id.y < uniforms.M && w + local_id.x < uniforms.K) {
        tileQ[TILE_SIZE * local_id.y + local_id.x] = q[qOffset + local_id.y * uniforms.K + w + local_id.x];
      }
      if (n + local_id.y < uniforms.N && w + local_id.x < uniforms.K) {
        var idx = TILE_SIZE * local_id.y + local_id.x;
      ${K&&x?`
              if (n + local_id.y < past_sequence_length) {
                tileK[idx] = past_key[pastKeyOffset + (n + local_id.y) * uniforms.K + w + local_id.x];
              } else if (n + local_id.y - past_sequence_length < uniforms.kv_sequence_length) {
                tileK[idx] = key[kOffset + (n + local_id.y - past_sequence_length) * uniforms.K + w + local_id.x];
              }`:`
          if (n + local_id.y < uniforms.kv_sequence_length) {
            tileK[idx] = key[kOffset + (n + local_id.y) * uniforms.K + w + local_id.x];
          }`}
      ${x?`if (n + local_id.y < present_sequence_length) {
        present_key[presentKeyOffset + (n + local_id.y) * uniforms.K + w + local_id.x] = tileK[idx];
      }`:""}
      }
      workgroupBarrier();

      for (var k: u32 = 0u; k < TILE_SIZE && w+k < uniforms.K; k++) {
          value += ${ze}(tileQ[TILE_SIZE * local_id.y + k] * tileK[TILE_SIZE * local_id.x + k]);
      }

      workgroupBarrier();
    }

    if (global_id.y < uniforms.M && global_id.x < total_sequence_length) {
      let headOffset = workgroup_id.z * uniforms.M * uniforms.N;
      let outputIdx = headOffset + global_id.y * uniforms.N + global_id.x;
      var sum: f32 = ${(()=>{switch(H){case 1:return"value";case 2:return"value.x + value.y";case 4:return"value.x + value.y + value.z + value.w";default:throw new Error(`Unsupported components: ${H}`)}})()};
        output[outputIdx] = ${qe.type.value} (sum * uniforms.alpha) + ${o?"attention_bias[outputIdx]":"0.0"};
    }
  }`};return{name:"AttentionProbs",shaderCache:{hint:`${H};${o!==void 0};${s!==void 0};${e}`,inputDependencies:ee},getRunData:()=>({outputs:ae,dispatchGroup:F,programUniforms:G}),getShaderSource:B}},oy=(e,r,a,s,o,p,d=void 0,g=void 0)=>{let m=p+o.kvSequenceLength,_=o.nReps?o.nReps:1,v=o.vHiddenSize*_,x=e>1&&s,T=o.kvNumHeads?o.kvNumHeads:o.numHeads,C=x?[o.batchSize,T,m,o.headSize]:void 0,A=[o.batchSize,o.sequenceLength,v],R=12,H={x:Math.ceil(o.vHeadSize/R),y:Math.ceil(o.sequenceLength/R),z:o.batchSize*o.numHeads},U=[{type:12,data:o.sequenceLength},{type:12,data:m},{type:12,data:o.vHeadSize},{type:12,data:o.numHeads},{type:12,data:o.headSize},{type:12,data:v},{type:12,data:p},{type:12,data:o.kvSequenceLength},{type:12,data:_}],P=x&&s&&ge.size(s.dims)>0,F=["type","type"];P&&F.push("type"),d&&F.push("type"),g&&F.push("type");let G=[{dims:A,dataType:r.dataType,gpuDataType:0}];x&&G.push({dims:C,dataType:r.dataType,gpuDataType:0});let K=ee=>{let ae=$e("probs",r.dataType,r.dims),B=$e("v",a.dataType,a.dims),me=[ae,B];P&&me.push($e("past_value",s.dataType,s.dims));let _e=d?$e("seq_lens",d.dataType,d.dims):void 0;d&&me.push(_e);let Re=g?$e("total_sequence_length_input",g.dataType,g.dims):void 0;g&&me.push(Re);let Ue=[je("output",r.dataType,A)];x&&Ue.push(je("present_value",r.dataType,C));let Me=[{name:"M",type:"u32"},{name:"K",type:"u32"},{name:"N",type:"u32"},{name:"num_heads",type:"u32"},{name:"head_size",type:"u32"},{name:"v_hidden_size",type:"u32"},{name:"past_sequence_length",type:"u32"},{name:"kv_sequence_length",type:"u32"},{name:"n_reps",type:"u32"}];return`
  const TILE_SIZE = ${R}u;
  var<workgroup> tileQ: array<${ae.type.value}, ${R*R}>;
  var<workgroup> tileV: array<${ae.type.value}, ${R*R}>;
  ${ee.registerUniforms(Me).declareVariables(...me,...Ue)}
  ${ee.mainStart([R,R,1])}
   let headIdx = workgroup_id.z % uniforms.num_heads;
   let batchIdx = workgroup_id.z / uniforms.num_heads;
   let kvHeadIdx = ${_===1?"headIdx":"headIdx / uniforms.n_reps"};
   let kv_num_heads = ${_===1?"uniforms.num_heads":"uniforms.num_heads / uniforms.n_reps"};
   let m = global_id.y;
   let n = global_id.x;
   let sequence_length = uniforms.M;
   var total_sequence_length = uniforms.K;
   ${Fu(_e,Re,!0)}
   let offsetA = workgroup_id.z * uniforms.M * uniforms.K + m * uniforms.K;
   let absKvHeadIdx = batchIdx * kv_num_heads + kvHeadIdx; // kvHeadIdx is relative to the batch
   ${P&&x?"let pastValueOffset = absKvHeadIdx * uniforms.N * uniforms.past_sequence_length + n;":""};
   let vOffset = absKvHeadIdx * uniforms.N * uniforms.kv_sequence_length + n;
   ${x?"let presentValueOffset = absKvHeadIdx * uniforms.N * uniforms.K + n;":""}
   var value = ${ae.type.storage}(0);
   for (var w: u32 = 0u; w < uniforms.K; w += TILE_SIZE) {
      if (m < uniforms.M && w + local_id.x < uniforms.K) {
        tileQ[TILE_SIZE * local_id.y + local_id.x] = probs[offsetA + w + local_id.x];
      }
      if (n < uniforms.N && w + local_id.y < uniforms.K) {
        var idx = TILE_SIZE * local_id.y + local_id.x;
        ${P&&x?`
        if (w + local_id.y < past_sequence_length) {
          tileV[idx] = past_value[pastValueOffset + (w + local_id.y) * uniforms.N];
        } else if (w + local_id.y - past_sequence_length < uniforms.kv_sequence_length) {
          tileV[idx] = v[vOffset + (w + local_id.y - past_sequence_length) * uniforms.N];
        }
      `:`
            if (w + local_id.y < uniforms.kv_sequence_length) {
              tileV[idx] = v[vOffset + (w + local_id.y) * uniforms.N];
            }`}
        ${x?`
            if (w + local_id.y < present_sequence_length) {
          present_value[presentValueOffset + (w + local_id.y) * uniforms.N] = tileV[idx];
        }`:""}
      }
     workgroupBarrier();
     for (var k: u32 = 0u; k < TILE_SIZE && w+k < total_sequence_length; k++) {
       value += tileQ[TILE_SIZE * local_id.y + k] * tileV[TILE_SIZE * k + local_id.x];
     }
     workgroupBarrier();
   }

   // we need to transpose output from BNSH_v to BSND_v
   if (m < uniforms.M && n < uniforms.N) {
     let outputIdx = batchIdx * uniforms.M * uniforms.v_hidden_size + m * uniforms.v_hidden_size
       + headIdx * uniforms.N + n;
     output[outputIdx] = value;
   }
  }`};return{name:"AttentionScore",shaderCache:{hint:`${s!==void 0};${e}`,inputDependencies:F},getRunData:()=>({outputs:G,dispatchGroup:H,programUniforms:U}),getShaderSource:K}},Fo=(e,r,a,s,o,p,d,g,m,_,v=void 0,x=void 0)=>{let T=Math.min(e.outputCount,1+(d?1:0)+(g?1:0)),C=T>1?d:void 0,A=T>1?g:void 0,R=T>1?_.pastSequenceLength:0,H=R+_.kvSequenceLength,U=m&&ge.size(m.dims)>0?m:void 0,P=[r,a];C&&ge.size(C.dims)>0&&P.push(C),U&&P.push(U),v&&P.push(v),x&&P.push(x);let F=e.compute(sy(T,r,a,C,U,_,R,v,x),{inputs:P,outputs:T>1?[-1,1]:[-1]})[0];e.compute(ny(F,_.batchSize,_.numHeads,R,_.sequenceLength,H,v,x),{inputs:v&&x?[F,v,x]:[F],outputs:[]});let G=[F,s];A&&ge.size(A.dims)>0&&G.push(A),v&&G.push(v),x&&G.push(x),e.compute(oy(T,F,s,A,_,R,v,x),{inputs:G,outputs:T>1?[0,2]:[0]})},uy=(e,r)=>{let a=[r.batchSize,r.numHeads,r.sequenceLength,r.headSize],s=r.sequenceLength,o=r.inputHiddenSize,p=r.headSize,d=12,g={x:Math.ceil(r.headSize/d),y:Math.ceil(r.sequenceLength/d),z:r.batchSize*r.numHeads},m=[e.inputs[0],e.inputs[1],e.inputs[2]],_=[{type:12,data:s},{type:12,data:o},{type:12,data:p},{type:12,data:r.numHeads},{type:12,data:r.headSize},{type:12,data:r.hiddenSize},{type:12,data:r.hiddenSize+r.hiddenSize+r.vHiddenSize}],v=x=>{let T=je("output_q",m[0].dataType,a),C=je("output_k",m[0].dataType,a),A=je("output_v",m[0].dataType,a),R=$e("input",m[0].dataType,m[0].dims),H=$e("weight",m[1].dataType,m[1].dims),U=$e("bias",m[2].dataType,m[2].dims),P=R.type.storage,F=[{name:"M",type:"u32"},{name:"K",type:"u32"},{name:"N",type:"u32"},{name:"num_heads",type:"u32"},{name:"head_size",type:"u32"},{name:"hidden_size",type:"u32"},{name:"ldb",type:"u32"}];return`
  const TILE_SIZE = ${d}u;
  var<workgroup> tileInput: array<${P}, ${d*d}>;
  var<workgroup> tileWeightQ: array<${P}, ${d*d}>;
  var<workgroup> tileWeightK: array<${P}, ${d*d}>;
  var<workgroup> tileWeightV: array<${P}, ${d*d}>;
  ${x.registerUniforms(F).declareVariables(R,H,U,T,C,A)}
  ${x.mainStart([d,d,1])}
    let batchIndex = workgroup_id.z / uniforms.num_heads;
    let headNumber = workgroup_id.z % uniforms.num_heads;
    let m = global_id.y;
    let n = global_id.x;

    let inputOffset = batchIndex * (uniforms.M * uniforms.K) + m * uniforms.K;
    let biasOffsetQ = headNumber * uniforms.head_size;
    let biasOffsetK = uniforms.hidden_size + biasOffsetQ;
    let biasOffsetV = uniforms.hidden_size + biasOffsetK;

    var valueQ = ${P}(0);
    var valueK = ${P}(0);
    var valueV = ${P}(0);
    for (var w: u32 = 0u; w < uniforms.K; w += TILE_SIZE) {
      if (m < uniforms.M && w + local_id.x < uniforms.K) {
        tileInput[TILE_SIZE * local_id.y + local_id.x] = input[inputOffset + w + local_id.x];
      }
      if (n < uniforms.N && w + local_id.y < uniforms.K) {
        let offset = n + (w + local_id.y) * uniforms.ldb;
        tileWeightQ[TILE_SIZE * local_id.y + local_id.x] = weight[biasOffsetQ + offset];
        tileWeightK[TILE_SIZE * local_id.y + local_id.x] = weight[biasOffsetK + offset];
        tileWeightV[TILE_SIZE * local_id.y + local_id.x] = weight[biasOffsetV + offset];
      }
      workgroupBarrier();
      for (var k: u32 = 0u; k<TILE_SIZE && w+k < uniforms.K; k++) {
        let inputTileOffset = TILE_SIZE * local_id.y + k;
        let weightTileOffset = TILE_SIZE * k + local_id.x;
        valueQ += tileInput[inputTileOffset] * tileWeightQ[weightTileOffset];
        valueK += tileInput[inputTileOffset] * tileWeightK[weightTileOffset];
        valueV += tileInput[inputTileOffset] * tileWeightV[weightTileOffset];
      }

      workgroupBarrier();
    }

    let headOffset = (m * uniforms.N + n) % uniforms.head_size;
    valueQ += bias[headOffset + biasOffsetQ];
    valueK += bias[headOffset + biasOffsetK];
    valueV += bias[headOffset + biasOffsetV];

    let offset = workgroup_id.z * uniforms.M * uniforms.N;
    if (m < uniforms.M && n < uniforms.N) {
      let outputIdx = offset + m * uniforms.N + n;
      output_q[outputIdx] = valueQ;
      output_k[outputIdx] = valueK;
      output_v[outputIdx] = valueV;
    }
  }`};return e.compute({name:"AttentionPrepare",shaderCache:{inputDependencies:["type","type","type"]},getRunData:()=>({outputs:[{dims:a,dataType:e.inputs[0].dataType,gpuDataType:0},{dims:a,dataType:e.inputs[0].dataType,gpuDataType:0},{dims:a,dataType:e.inputs[0].dataType,gpuDataType:0}],dispatchGroup:g,programUniforms:_}),getShaderSource:v},{inputs:m,outputs:[-1,-1,-1]})},X0=(e,r)=>{let a=ay(e.inputs,r),[s,o,p]=uy(e,a);return Fo(e,s,o,p,e.inputs[4],void 0,void 0,void 0,e.inputs[5],a)}}),ly,dy,py,Y0,r1=Ee(()=>{bi(),ut(),ct(),Jt(),ft(),ly=(e,r)=>{if(!e||e.length!==5)throw new Error("BatchNormalization requires 5 inputs");let a=(s,o,p)=>{let d=o.length;if(d!==s.length)throw new Error(`${p}: num dimensions != ${d}`);o.forEach((g,m)=>{if(g!==s[m])throw new Error(`${p}: dim[${m}] do not match`)})};if(e[0].dims.length>1){let s=r.format==="NHWC"?r.spatial?e[0].dims.slice(-1):e[0].dims.slice(-1).concat(e[0].dims.slice(1,e[0].dims.length-1)):e[0].dims.slice(1,r.spatial?2:void 0);a(e[1].dims,s,"Invalid input scale"),a(e[2].dims,s,"Invalid input B"),a(e[3].dims,s,"Invalid input mean"),a(e[4].dims,s,"Invalid input var")}else a(e[1].dims,[1],"Invalid input scale"),a(e[2].dims,[1],"Invalid input B"),a(e[3].dims,[1],"Invalid input mean"),a(e[4].dims,[1],"Invalid input var")},dy=(e,r)=>{let{epsilon:a,spatial:s,format:o}=r,p=e[0].dims,d=s?Yt(p[p.length-1]):1,g=o==="NHWC"&&p.length>1?d:1,m=ge.size(p)/d,_=s,v=_?p.length:p,x=$e("x",e[0].dataType,e[0].dims,d),T=$e("scale",e[1].dataType,e[1].dims,g),C=$e("bias",e[2].dataType,e[2].dims,g),A=$e("inputMean",e[3].dataType,e[3].dims,g),R=$e("inputVar",e[4].dataType,e[4].dims,g),H=je("y",e[0].dataType,v,d),U=()=>{let F="";if(s)F=`let cOffset = ${p.length===1?"0u":o==="NHWC"?`outputIndices[${p.length-1}] / ${d}`:"outputIndices[1]"};`;else if(o==="NCHW")F=`
            ${H.indicesSet("outputIndices","0","0")}
            let cOffset = ${H.indicesToOffset("outputIndices")};`;else{F=`var cIndices = ${T.type.indices}(0);
                       cIndices[0] = outputIndices[${p.length-1}];`;for(let G=1;G<T.rank;G++)F+=`cIndices[${G}] = outputIndices[${G}];`;F+=`let cOffset = ${T.indicesToOffset("cIndices")};`}return F},P=F=>`
  const epsilon = ${a};
  ${F.registerUniform("outputSize","u32").declareVariables(x,T,C,A,R,H)}
  ${F.mainStart()}
  ${F.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
    var outputIndices = ${H.offsetToIndices(`global_idx * ${d}`)};
    ${U()}
    let scale = ${T.getByOffset("cOffset")};
    let bias = ${C.getByOffset("cOffset")};
    let inputMean = ${A.getByOffset("cOffset")};
    let inputVar = ${R.getByOffset("cOffset")};
    let x = ${x.getByOffset("global_idx")};
    let value = (x - inputMean) * inverseSqrt(inputVar + epsilon) * scale + bias;
    ${H.setByOffset("global_idx","value")}
  }`;return{name:"BatchNormalization",shaderCache:{hint:`${r.epsilon}_${r.format}_${s}_${d}`,inputDependencies:_?["rank","type","type","type","type"]:void 0},getShaderSource:P,getRunData:()=>({outputs:[{dims:e[0].dims,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(m/64)},programUniforms:_?[{type:12,data:m},...Je(p)]:[{type:12,data:m}]})}},py=e=>Nt(e),Y0=(e,r)=>{let{inputs:a,outputCount:s}=e,o=py({...r,outputCount:s});if(Ft.webgpu.validateInputContent&&ly(a,o),r.trainingMode)throw new Error("BatchNormalization trainingMode is not supported yet.");e.compute(dy(a,o))}}),cy,hy,J0,i1=Ee(()=>{ct(),ft(),cy=e=>{if(e[0].dims.length!==3)throw new Error("input should have 3 dimensions");if(![320,640,1280].includes(e[0].dims[2]))throw new Error("number of channels should be 320, 640 or 1280");if(e[1].dims.length!==1)throw new Error("bias is expected to have 1 dimensions");if(e[0].dims[2]!==e[1].dims[0])throw new Error("last dimension of input and bias are not the same")},hy=e=>{let r=e[0].dims,a=e[0].dims[2],s=ge.size(r)/4,o=e[0].dataType,p=$e("input",o,r,4),d=$e("bias",o,[a],4),g=$e("residual",o,r,4),m=je("output",o,r,4);return{name:"BiasAdd",getRunData:()=>({outputs:[{dims:r,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(s/64)}}),getShaderSource:_=>`
  const channels = ${a}u / 4;
  ${_.declareVariables(p,d,g,m)}

  ${_.mainStart()}
    ${_.guardAgainstOutOfBoundsWorkgroupSizes(s)}
    let value = ${p.getByOffset("global_idx")}
      + ${d.getByOffset("global_idx % channels")} + ${g.getByOffset("global_idx")};
    ${m.setByOffset("global_idx","value")}
  }`}},J0=e=>{cy(e.inputs),e.compute(hy(e.inputs))}}),fy,Bt,eb,tb,rb,ib,ab,nb,sb,ob,ub,my,lb,db,pb,cb,qo,hb,el,fb,mb,gb,yb,_b,wb,bb,$b,vb,xb,Sb,Tb,kb,Eb,Ib,zb,zd,Cb,pp,cp,Ab,Ob,Rb,gy,yy,Bb,Np=Ee(()=>{ut(),ct(),Jt(),ft(),fy=(e,r,a,s,o,p,d)=>{let g=Math.ceil(r/4),m="";typeof o=="string"?m=`${o}(a)`:m=o("a");let _=$e("inputData",a,[g],4),v=je("outputData",s,[g],4),x=[{name:"vec_size",type:"u32"}];return d&&x.push(...d),`
      ${e.registerUniforms(x).declareVariables(_,v)}

  ${p??""}

  ${e.mainStart()}
    ${e.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}

    let a = ${_.getByOffset("global_idx")};
    ${v.setByOffset("global_idx",m)}
  }`},Bt=(e,r,a,s,o,p=e.dataType,d,g)=>{let m=[{type:12,data:Math.ceil(ge.size(e.dims)/4)}];return d&&m.push(...d),{name:r,shaderCache:{hint:o,inputDependencies:["type"]},getShaderSource:_=>fy(_,ge.size(e.dims),e.dataType,p,a,s,g),getRunData:_=>({outputs:[{dims:e.dims,dataType:p}],dispatchGroup:{x:Math.ceil(ge.size(_[0].dims)/64/4)},programUniforms:m})}},eb=e=>{e.compute(Bt(e.inputs[0],"Abs","abs"))},tb=e=>{e.compute(Bt(e.inputs[0],"Acos","acos"))},rb=e=>{e.compute(Bt(e.inputs[0],"Acosh","acosh"))},ib=e=>{e.compute(Bt(e.inputs[0],"Asin","asin"))},ab=e=>{e.compute(Bt(e.inputs[0],"Asinh","asinh"))},nb=e=>{e.compute(Bt(e.inputs[0],"Atan","atan"))},sb=e=>{e.compute(Bt(e.inputs[0],"Atanh","atanh"))},ob=e=>Nt(e),ub=(e,r)=>{let a;switch(r.to){case 10:a="vec4<f16>";break;case 1:a="vec4<f32>";break;case 12:a="vec4<u32>";break;case 6:a="vec4<i32>";break;case 9:a="vec4<bool>";break;default:throw new RangeError(`not supported type (specified in attribute 'to' from 'Cast' operator): ${r.to}`)}e.compute(Bt(e.inputs[0],"Cast",a,void 0,r.cacheKey,r.to))},my=e=>{let r,a,s=e.length>=2&&e[1].data!==0,o=e.length>=3&&e[2].data!==0;switch(e[0].dataType){case 1:r=s?e[1].getFloat32Array()[0]:-34028234663852886e22,a=o?e[2].getFloat32Array()[0]:34028234663852886e22;break;case 10:r=s?e[1].getUint16Array()[0]:64511,a=o?e[2].getUint16Array()[0]:31743;break;default:throw new Error("Unsupport data type")}return Nt({min:r,max:a})},lb=(e,r)=>{let a=r||my(e.inputs),s=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"Clip",o=>`clamp(${o}, vec4<${s}>(uniforms.min), vec4<${s}>(uniforms.max))`,void 0,a.cacheKey,void 0,[{type:e.inputs[0].dataType,data:a.min},{type:e.inputs[0].dataType,data:a.max}],[{name:"min",type:s},{name:"max",type:s}]),{inputs:[0]})},db=e=>{e.compute(Bt(e.inputs[0],"Ceil","ceil"))},pb=e=>{e.compute(Bt(e.inputs[0],"Cos","cos"))},cb=e=>{e.compute(Bt(e.inputs[0],"Cosh","cosh"))},qo=e=>Nt(e),hb=(e,r)=>{let a=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"Elu",s=>`elu_vf32(${s})`,`
  const elu_alpha_ = ${a}(${r.alpha});

  fn elu_f32(a: ${a}) -> ${a} {
  return select((exp(a) - 1.0) * elu_alpha_, a, a >= 0.0);
  }

  fn elu_vf32(v: vec4<${a}>) -> vec4<${a}> {
  return vec4(elu_f32(v.x), elu_f32(v.y), elu_f32(v.z), elu_f32(v.w));
  }`,r.cacheKey))},el=(e="f32")=>`
const r0: ${e} = 0.3275911;
const r1: ${e} = 0.254829592;
const r2: ${e} = -0.284496736;
const r3: ${e} = 1.421413741;
const r4: ${e} = -1.453152027;
const r5: ${e} = 1.061405429;

fn erf_vf32(v: vec4<${e}>) -> vec4<${e}> {
  let absv = abs(v);
  let x = 1.0 / (1.0 + r0 * absv);
  return sign(v) * (1.0 - ((((r5 * x + r4) * x + r3) * x + r2) * x + r1) * x * exp(-absv * absv));
}`,fb=e=>{let r=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"Erf",a=>`erf_vf32(${a})`,el(r)))},mb=e=>{e.compute(Bt(e.inputs[0],"Exp","exp"))},gb=e=>{e.compute(Bt(e.inputs[0],"Floor","floor"))},yb=e=>{let r=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"Gelu",a=>`0.5 * ${a} * (1.0 + erf_vf32(${a} * 0.7071067811865475))`,el(r)))},_b=(e,r)=>{let a=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"LeakyRelu",s=>`select(leaky_relu_alpha_ * ${s}, ${s}, ${s} >= vec4<${a}>(0.0))`,`const leaky_relu_alpha_ = ${a}(${r.alpha});`,r.cacheKey))},wb=e=>{e.compute(Bt(e.inputs[0],"Not",r=>`!${r}`))},bb=e=>{e.compute(Bt(e.inputs[0],"Neg",r=>`-${r}`))},$b=e=>{e.compute(Bt(e.inputs[0],"Reciprocal",r=>`1.0/${r}`))},vb=e=>{let r=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"Relu",a=>`select(vec4<${r}>(0.0), ${a}, ${a} > vec4<${r}>(0.0))`))},xb=e=>{e.compute(Bt(e.inputs[0],"Sigmoid",r=>`(1.0 / (1.0 + exp(-${r})))`))},Sb=e=>Nt(e),Tb=(e,r)=>{let a=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"HardSigmoid",s=>`max(vec4<${a}>(0.0), min(vec4<${a}>(1.0), ${r.alpha} * ${s} + vec4<${a}>(${r.beta})))`,void 0,r.cacheKey))},kb=e=>{e.compute(Bt(e.inputs[0],"Sin","sin"))},Eb=e=>{e.compute(Bt(e.inputs[0],"Sinh","sinh"))},Ib=e=>{e.compute(Bt(e.inputs[0],"Sqrt","sqrt"))},zb=e=>{e.compute(Bt(e.inputs[0],"Tan","tan"))},zd=e=>`sign(${e}) * (1 - exp(-2 * abs(${e}))) / (1 + exp(-2 * abs(${e})))`,Cb=e=>{e.compute(Bt(e.inputs[0],"Tanh",zd))},pp=(e="f32")=>`
const fast_gelu_a: ${e} = 0.5;
const fast_gelu_b: ${e} = 0.7978845608028654;
const fast_gelu_c: ${e} = 0.035677408136300125;

fn tanh_v(v: vec4<${e}>) -> vec4<${e}> {
  return ${zd("v")};
}
`,cp=e=>`(fast_gelu_a + fast_gelu_a * tanh_v(${e} * (fast_gelu_c * ${e} * ${e} + fast_gelu_b))) * ${e}`,Ab=e=>{let r=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"FastGelu",cp,pp(r),void 0,e.inputs[0].dataType))},Ob=(e,r)=>{let a=Or(e.inputs[0].dataType);return e.compute(Bt(e.inputs[0],"ThresholdedRelu",s=>`select(vec4<${a}>(0.0), ${s}, ${s} > thresholded_relu_alpha_)`,`const thresholded_relu_alpha_ = vec4<${a}>(${r.alpha});`,r.cacheKey)),0},Rb=e=>{e.compute(Bt(e.inputs[0],"Log","log"))},gy=(e,r)=>`
const alpha = vec4<${e}>(${r});
const one = ${e}(1.0);
const zero = ${e}(0.0);

fn quick_gelu_impl(x: vec4<${e}>) -> vec4<${e}> {
  let v = x *alpha;
  var x1 : vec4<${e}>;
  for (var i = 0; i < 4; i = i + 1) {
    if (v[i] >= zero) {
      x1[i] = one / (one + exp(-v[i]));
    } else {
      x1[i] = one - one / (one + exp(v[i]));
    }
  }
  return x * x1;
}
`,yy=e=>`quick_gelu_impl(${e})`,Bb=(e,r)=>{let a=Or(e.inputs[0].dataType);e.compute(Bt(e.inputs[0],"QuickGelu",yy,gy(a,r.alpha),r.cacheKey,e.inputs[0].dataType))}}),_y,wy,Mb,a1=Ee(()=>{ct(),ft(),Np(),_y=e=>{if(e[0].dims.length!==3)throw new Error("input should have 3 dimensions");if(![2560,5120,10240].includes(e[0].dims[2]))throw new Error("hidden state should be 2560, 5120 or 10240");if(e[1].dims.length!==1)throw new Error("bias is expected to have 1 dimensions");if(e[0].dims[2]!==e[1].dims[0])throw new Error("last dimension of input and bias are not the same")},wy=e=>{let r=e[0].dims.slice();r[2]=r[2]/2;let a=$e("input",e[0].dataType,e[0].dims,4),s=$e("bias",e[0].dataType,[e[0].dims[2]],4),o=je("output",e[0].dataType,r,4),p=ge.size(r)/4,d=yr(e[0].dataType);return{name:"BiasSplitGelu",getRunData:()=>({outputs:[{dims:r,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(p/64)}}),getShaderSource:g=>`
  const M_SQRT2 = sqrt(2.0);
  const halfChannels = ${e[0].dims[2]/4/2}u;

  ${g.declareVariables(a,s,o)}

  ${el(d)}

  ${g.mainStart()}
    ${g.guardAgainstOutOfBoundsWorkgroupSizes(p)}
    let biasIdx = global_idx % halfChannels;
    let batchIndex = global_idx / halfChannels;
    let inputOffset = biasIdx + batchIndex * halfChannels * 2;
    let valueLeft = input[inputOffset] + bias[biasIdx];
    let valueRight = input[inputOffset + halfChannels] + bias[biasIdx + halfChannels];
    let geluRight = valueRight * 0.5 * (erf_vf32(valueRight / M_SQRT2) + 1);

    ${o.setByOffset("global_idx","valueLeft * geluRight")}
  }`}},Mb=e=>{_y(e.inputs),e.compute(wy(e.inputs))}}),by,$y,Di,Db,Nb,Pb,Ub,Lb,qb,Vb,Wb,Gb,Fb,n1=Ee(()=>{ut(),ct(),ft(),by=(e,r,a,s,o,p,d,g,m,_,v,x)=>{let T,C;typeof g=="string"?T=C=(P,F)=>`${g}((${P}),(${F}))`:typeof g=="function"?T=C=g:(T=g.scalar,C=g.vector);let A=je("outputData",v,s.length,4),R=$e("aData",m,r.length,4),H=$e("bData",_,a.length,4),U;if(o)if(p){let P=ge.size(r)===1,F=ge.size(a)===1,G=r.length>0&&r[r.length-1]%4===0,K=a.length>0&&a[a.length-1]%4===0;P||F?U=A.setByOffset("global_idx",C(P?`${R.type.value}(${R.getByOffset("0")}.x)`:R.getByOffset("global_idx"),F?`${H.type.value}(${H.getByOffset("0")}.x)`:H.getByOffset("global_idx"))):U=`
            let outputIndices = ${A.offsetToIndices("global_idx * 4u")};
            let offsetA = ${R.broadcastedIndicesToOffset("outputIndices",A)};
            let offsetB = ${H.broadcastedIndicesToOffset("outputIndices",A)};
            ${A.setByOffset("global_idx",C(d||G?R.getByOffset("offsetA / 4u"):`${R.type.value}(${R.getByOffset("offsetA / 4u")}[offsetA % 4u])`,d||K?H.getByOffset("offsetB / 4u"):`${H.type.value}(${H.getByOffset("offsetB / 4u")}[offsetB % 4u])`))}
          `}else U=A.setByOffset("global_idx",C(R.getByOffset("global_idx"),H.getByOffset("global_idx")));else{if(!p)throw new Error("no necessary to use scalar implementation for element-wise binary op implementation.");let P=(F,G,K="")=>{let ee=`aData[indexA${G}][componentA${G}]`,ae=`bData[indexB${G}][componentB${G}]`;return`
            let outputIndices${G} = ${A.offsetToIndices(`global_idx * 4u + ${G}u`)};
            let offsetA${G} = ${R.broadcastedIndicesToOffset(`outputIndices${G}`,A)};
            let offsetB${G} = ${H.broadcastedIndicesToOffset(`outputIndices${G}`,A)};
            let indexA${G} = offsetA${G} / 4u;
            let indexB${G} = offsetB${G} / 4u;
            let componentA${G} = offsetA${G} % 4u;
            let componentB${G} = offsetB${G} % 4u;
            ${F}[${G}] = ${K}(${T(ee,ae)});
          `};v===9?U=`
            var data = vec4<u32>(0);
            ${P("data",0,"u32")}
            ${P("data",1,"u32")}
            ${P("data",2,"u32")}
            ${P("data",3,"u32")}
            outputData[global_idx] = dot(vec4<u32>(0x1, 0x100, 0x10000, 0x1000000), vec4<u32>(data));`:U=`
            ${P("outputData[global_idx]",0)}
            ${P("outputData[global_idx]",1)}
            ${P("outputData[global_idx]",2)}
            ${P("outputData[global_idx]",3)}
          `}return`
        ${e.registerUniform("vec_size","u32").declareVariables(R,H,A)}

        ${x??""}

        ${e.mainStart()}
        ${e.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}
        ${U}
      }`},$y=(e,r,a,s,o,p,d=a.dataType)=>{let g=a.dims.map(Number),m=s.dims.map(Number),_=!ge.areEqual(g,m),v=g,x=ge.size(g),T=!1,C=!1,A=[_];if(_){let R=ms.calcShape(g,m,!1);if(!R)throw new Error("Can't perform binary op on the given tensors");v=R.slice(),x=ge.size(v);let H=ge.size(g)===1,U=ge.size(m)===1,P=g.length>0&&g[g.length-1]%4===0,F=m.length>0&&m[m.length-1]%4===0;A.push(H),A.push(U),A.push(P),A.push(F);let G=1;for(let K=1;K<v.length;K++){let ee=g[g.length-K],ae=m[m.length-K];if(ee===ae)G*=ee;else break}G%4===0?(C=!0,T=!0):(H||U||P||F)&&(T=!0)}else T=!0;return A.push(T),{name:e,shaderCache:{hint:r+A.map(R=>R.toString()).join("_"),inputDependencies:["rank","rank"]},getShaderSource:R=>by(R,g,m,v,T,_,C,o,a.dataType,s.dataType,d,p),getRunData:()=>({outputs:[{dims:v,dataType:d}],dispatchGroup:{x:Math.ceil(x/64/4)},programUniforms:[{type:12,data:Math.ceil(ge.size(v)/4)},...Je(g,m,v)]})}},Di=(e,r,a,s,o,p)=>{e.compute($y(r,o??"",e.inputs[0],e.inputs[1],a,s,p))},Db=e=>{Di(e,"Add",(r,a)=>`${r}+${a}`)},Nb=e=>{Di(e,"Div",(r,a)=>`${r}/${a}`)},Pb=e=>{Di(e,"Equal",{scalar:(r,a)=>`u32(${r}==${a})`,vector:(r,a)=>`vec4<u32>(${r}==${a})`},void 0,void 0,9)},Ub=e=>{Di(e,"Mul",(r,a)=>`${r}*${a}`)},Lb=e=>{let r=$e("input",e.inputs[0].dataType,e.inputs[0].dims).type.value;Di(e,"Pow",{scalar:(a,s)=>`pow_custom(${a},${s})`,vector:(a,s)=>`pow_vector_custom(${a},${s})`},`
    fn pow_custom(a : ${r}, b : ${r}) -> ${r} {
      if (b == ${r}(0.0)) {
        return ${r}(1.0);
      } else if (a < ${r}(0.0) && f32(b) != floor(f32(b))) {
        return ${r}(pow(f32(a), f32(b))); // NaN
      }
      return select(sign(a), ${r}(1.0), round(f32(abs(b) % ${r}(2.0))) != 1.0) * ${r}(${r==="i32"?"round":""}(pow(f32(abs(a)), f32(b))));
    }
    fn pow_vector_custom(a : vec4<${r}>, b : vec4<${r}>) -> vec4<${r}> {
      // TODO: implement vectorized pow
      return vec4<${r}>(pow_custom(a.x, b.x), pow_custom(a.y, b.y), pow_custom(a.z, b.z), pow_custom(a.w, b.w));
    }
      `)},qb=e=>{Di(e,"Sub",(r,a)=>`${r}-${a}`)},Vb=e=>{Di(e,"Greater",{scalar:(r,a)=>`u32(${r}>${a})`,vector:(r,a)=>`vec4<u32>(${r}>${a})`},void 0,void 0,9)},Wb=e=>{Di(e,"Less",{scalar:(r,a)=>`u32(${r}<${a})`,vector:(r,a)=>`vec4<u32>(${r}<${a})`},void 0,void 0,9)},Gb=e=>{Di(e,"GreaterOrEqual",{scalar:(r,a)=>`u32(${r}>=${a})`,vector:(r,a)=>`vec4<u32>(${r}>=${a})`},void 0,void 0,9)},Fb=e=>{Di(e,"LessOrEqual",{scalar:(r,a)=>`u32(${r}<=${a})`,vector:(r,a)=>`vec4<u32>(${r}<=${a})`},void 0,void 0,9)}}),vy,xy,Sy,Ty,Hb,jb,s1=Ee(()=>{ut(),ct(),Jt(),ft(),vy=(e,r)=>{if(!e||e.length<1)throw new Error("too few inputs");let a=0,s=e[a],o=s.dataType,p=s.dims.length;e.forEach((d,g)=>{if(g!==a){if(d.dataType!==o)throw new Error("input tensors should be one type");if(d.dims.length!==p)throw new Error("input tensors should have the same shape");d.dims.forEach((m,_)=>{if(_!==r&&m!==s.dims[_])throw new Error("non concat dimensions must match")})}})},xy=(e,r)=>`
  fn calculateInputIndex(index: u32) -> u32 {
    let sizeInConcatAxis = array<u32, ${e}u>(${r});
    for (var i: u32 = 0u; i < ${e}; i += 1u ) {
      if (index < sizeInConcatAxis[i]) {
        return i;
      }
    }
    return ${e}u;
  }`,Sy=(e,r)=>{let a=e.length,s=[];for(let o=0;o<a;++o){let p=r.setByOffset("global_idx",e[o].getByIndices("indices"));a===1?s.push(p):o===0?s.push(`if (inputIndex == ${o}u) { ${p} }`):o===a-1?s.push(`else { ${p} }`):s.push(`else if (inputIndex == ${o}) { ${p} }`)}return s.join(`
`)},Ty=(e,r,a,s)=>{let o=ge.size(a),p=new Array(e.length),d=new Array(e.length),g=0,m=[],_=[],v=[{type:12,data:o}];for(let R=0;R<e.length;++R)g+=e[R].dims[r],p[R]=g,_.push(e[R].dims.length),d[R]=$e(`input${R}`,s,_[R]),m.push("rank"),v.push({type:12,data:p[R]});for(let R=0;R<e.length;++R)v.push(...Je(e[R].dims));v.push(...Je(a));let x=je("output",s,a.length),T=x.indicesGet("indices",r),C=Array.from(Array(p.length).keys()).map(R=>`uniforms.sizeInConcatAxis${R}`).join(","),A=R=>`

  ${(()=>{R.registerUniform("outputSize","u32");for(let H=0;H<e.length;H++)R.registerUniform(`sizeInConcatAxis${H}`,"u32");return R.declareVariables(...d,x)})()}

  ${xy(p.length,C)}

  ${R.mainStart()}
    ${R.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}

    var indices = ${x.offsetToIndices("global_idx")};

    let inputIndex = calculateInputIndex(${T});
    if (inputIndex != 0u) {
      let sizeInConcatAxis = array<u32, ${p.length}u>(${C});
      ${T} -= sizeInConcatAxis[inputIndex - 1u];
    }

    ${Sy(d,x)}
  }`;return{name:"Concat",shaderCache:{hint:`${r}`,inputDependencies:m},getRunData:()=>({outputs:[{dims:a,dataType:s}],dispatchGroup:{x:Math.ceil(o/64)},programUniforms:v}),getShaderSource:A}},Hb=(e,r)=>{let a=e.inputs,s=a[0].dims,o=ge.normalizeAxis(r.axis,s.length);vy(a,o);let p=s.slice();p[o]=a.reduce((g,m)=>g+(m.dims.length>o?m.dims[o]:0),0);let d=a.filter(g=>ge.size(g.dims)>0);e.compute(Ty(d,o,p,a[0].dataType),{inputs:d})},jb=e=>Nt({axis:e.axis})}),Vn,Wn,Gn,Pp,Hn=Ee(()=>{ut(),ct(),Vn=(e,r,a="f32")=>{switch(e.activation){case"Relu":return`value = max(value, ${r}(0.0));`;case"Sigmoid":return`value = (${r}(1.0) / (${r}(1.0) + exp(-value)));`;case"Clip":return`value = clamp(value, ${r}(${a}(uniforms.clip_min)), ${r}(${a}(uniforms.clip_max)));`;case"HardSigmoid":return`value = max(${r}(0.0), min(${r}(1.0), ${a}(uniforms.alpha) * value + ${a}(uniforms.beta)));`;case"LeakyRelu":return`value = select(${a}(uniforms.alpha) * value, value, value >= ${r}(0.0));`;case"Tanh":return`let e2x = exp(-2.0 * abs(value));
              value = sign(value) * (1.0 - e2x) / (1.0 + e2x);
        `;case"":return"";default:throw new Error(`Unsupported activation ${e.activation}`)}},Wn=(e,r)=>{e.activation==="Clip"?r.push({type:1,data:e.clipMax},{type:1,data:e.clipMin}):e.activation==="HardSigmoid"?r.push({type:1,data:e.alpha},{type:1,data:e.beta}):e.activation==="LeakyRelu"&&r.push({type:1,data:e.alpha})},Gn=(e,r)=>{e.activation==="Clip"?r.push({name:"clip_max",type:"f32"},{name:"clip_min",type:"f32"}):e.activation==="HardSigmoid"?r.push({name:"alpha",type:"f32"},{name:"beta",type:"f32"}):e.activation==="LeakyRelu"&&r.push({name:"alpha",type:"f32"})},Pp=e=>{let r=(e==null?void 0:e.activation)||"";if(r==="HardSigmoid"){let[a,s]=(e==null?void 0:e.activation_params)||[.2,.5];return{activation:r,alpha:a,beta:s}}else if(r==="Clip"){let[a,s]=(e==null?void 0:e.activation_params)||[w0,b0];return{activation:r,clipMax:s,clipMin:a}}else if(r==="LeakyRelu"){let[a]=(e==null?void 0:e.activation_params)||[.01];return{activation:r,alpha:a}}return{activation:r}}}),Tr,Kb,Up=Ee(()=>{Tr=(e,r)=>{switch(e){case 1:return r;case 2:return`vec2<${r}>`;case 3:return`vec3<${r}>`;case 4:return`vec4<${r}>`;default:throw new Error(`${e}-component is not supported.`)}},Kb=e=>`
      ${e?"value = value + getBiasByOutputCoords(coords);":""}
      `}),Zb,o1=Ee(()=>{Zb=e=>`
fn getIndexFromCoords4D(coords : vec4<i32>, shape : vec4<i32>) -> i32 {
  return dot(coords, vec4<i32>(
      shape.y * shape.z * shape.w, shape.z * shape.w, shape.w, 1));
}
fn getOutputIndexFromCoords(coords : vec4<i32>) -> i32 {
  return dot(coords, vec4<i32>(
    i32(${e}.x), i32(${e}.y), i32(${e}.z), 1));
}
`}),Wo,Lp,qp=Ee(()=>{ut(),ct(),ft(),Hn(),Wo=(e,r,a,s,o)=>{let p=s-a;return`
      ${Array.from({length:a}).map((d,g)=>`
      if (${Qe(r.shape,g,r.rank)} != 1) {
        ${r.indicesSet(e,g,Qe(o,g+p,s))}
      } else {
        ${r.indicesSet(e,g,0)}
      }`).join("")}
`},Lp=(e,r,a,s,o=!1,p)=>{let d=e[0].dims,g=e[1].dims,m=d[d.length-2],_=g[g.length-1],v=d[d.length-1],x=Yt(_),T=Yt(v),C=Yt(m),A=ge.size(a)/x/C,R=e.length>2,H=s?s.slice(0,-2):a.slice(0,-2),U=[ge.size(H),m,_],P=[{type:12,data:A},{type:12,data:m},{type:12,data:_},{type:12,data:v}];Wn(r,P),P.push(...Je(H,d,g)),R&&P.push(...Je(e[2].dims)),P.push(...Je(U));let F=G=>{let K=Bp("batch_dims",e[0].dataType,H.length),ee=$e("a",e[0].dataType,d.length,T),ae=$e("b",e[1].dataType,g.length,x),B=je("output",e[0].dataType,U.length,x),me=yr(B.type.tensor),_e=Vn(r,B.type.value,me),Re=[ee,ae],Ue="";if(R){let qe=o?x:1;Re.push($e("bias",e[2].dataType,e[2].dims.length,qe)),Ue=`${o?`value += bias[col / ${qe}];`:`value += ${B.type.value}(bias[row + i]);`}`}let Me=[{name:"output_size",type:"u32"},{name:"M",type:"u32"},{name:"N",type:"u32"},{name:"K",type:"u32"}];Gn(r,Me);let pe=()=>{let qe=`var a_data: ${ee.type.value};`;for(let Ve=0;Ve<T;Ve++)qe+=`
              let b_data${Ve} = b[(b_offset + (k + ${Ve}) * uniforms.N + col) / ${x}];`;for(let Ve=0;Ve<C;Ve++){qe+=`a_data = a[(a_offset + (row + ${Ve}) * uniforms.K + k) / ${T}];`;for(let ze=0;ze<T;ze++)qe+=`
            values[${Ve}] = fma(${ae.type.value}(a_data${T===1?"":`[${ze}]`}), b_data${ze}, values[${Ve}]);
`}return qe};return`
  ${G.registerUniforms(Me).registerInternalVariables(K).declareVariables(...Re,B)}
  ${G.mainStart()}
    ${G.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let col = (global_idx % (uniforms.N / ${x})) * ${x};
    var index1 = global_idx / (uniforms.N / ${x});
    let stride1 = uniforms.M / ${C};
    let row = (index1 % stride1) * ${C};
    let batch = index1 / stride1;

    ${a.length===2?"":`let batch_indices = ${K.offsetToIndices("batch")};`}

    var a_indices: ${ee.type.indices};
    ${Wo("a_indices",ee,ee.rank-2,K.rank,"batch_indices")}
    ${ee.indicesSet("a_indices",ee.rank-2,0)}
    ${ee.indicesSet("a_indices",ee.rank-1,0)}
    let a_offset = ${ee.indicesToOffset("a_indices")};

    var b_indices: ${ae.type.indices};
    ${Wo("b_indices",ae,ae.rank-2,K.rank,"batch_indices")}
    ${ae.indicesSet("b_indices",ae.rank-2,0)}
    ${ae.indicesSet("b_indices",ae.rank-1,0)}
    let b_offset = ${ae.indicesToOffset("b_indices")};
    var values: array<${B.type.value}, ${C}>;
    for (var k: u32 = 0u; k < uniforms.K; k = k + ${T}) {
      ${pe()}
    }
    for (var i = 0u; i < ${C}u; i++) {
      var value = values[i];
      ${Ue}
      ${_e}
      let cur_indices = ${B.type.indices}(batch, row + i, col);
      let offset = ${B.indicesToOffset("cur_indices")};
      ${B.setByOffset(`offset / ${x}`,"value")};
    }
  }
  `};return{name:"MatMulNaive",shaderCache:{hint:`${r.activation};${x};${T};${C};${o}`,inputDependencies:R?["rank","rank","rank"]:["rank","rank"]},getRunData:()=>({outputs:[{dims:p?p(a):a,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(A/64)},programUniforms:P}),getShaderSource:F}}}),ky,Ey,hp,Cd,Iy,fp,zy,sl,Vp=Ee(()=>{ut(),ct(),ft(),Hn(),qp(),Up(),ky=(e,r)=>e?`
        mm_Asub[inputRow][inputCol] = mm_readA(batch,
          kStart + inputRow,
          globalRowStart / innerElementSize + inputCol${r?", batchIndices":""});
        `:`
        mm_Asub[inputRow][inputCol] = mm_readA(batch,
          globalRow + innerRow,
          kStart / innerElementSize + inputCol${r?", batchIndices":""});
        `,Ey=(e,r)=>e?`
        let ACached0 = mm_Asub[k * innerElementSize][localRow];
        let ACached1 = mm_Asub[k * innerElementSize + 1][localRow];
        let ACached2 = mm_Asub[k * innerElementSize + 2][localRow];
        ${r===3?"":"let ACached3 = mm_Asub[k * innerElementSize + 3][localRow];"}
        for (var i = 0; i < rowPerThread; i = i + 1) {
          acc[i] = BCached0 * ACached0[i] + acc[i];
          acc[i] = BCached1 * ACached1[i] + acc[i];
          acc[i] = BCached2 * ACached2[i] + acc[i];
          ${r===3?"":"acc[i] = BCached3 * ACached3[i] + acc[i];"}
        }`:`
        for (var i = 0; i < rowPerThread; i = i + 1) {
          let ACached = mm_Asub[tileRow + i][k];
          acc[i] = BCached0 * ACached.x + acc[i];
          acc[i] = BCached1 * ACached.y + acc[i];
          acc[i] = BCached2 * ACached.z + acc[i];
          ${r===3?"":"acc[i] = BCached3 * ACached.w + acc[i];"}
        }`,hp=(e,r,a="f32",s,o=!1,p=32,d=!1,g=32)=>{let m=r[1]*e[1],_=r[0]*e[0],v=o?m:p,x=o?p:m,T=v/r[0],C=p/r[1];if(!((o&&T===4&&e[1]===4||!o&&(T===3||T===4))&&v%r[0]===0&&p%r[1]===0&&e[0]===4))throw new Error(`If transposeA ${o} is true, innerElementSize ${T} and workPerThread[1] ${e[1]} must be 4.
      Otherwise, innerElementSize ${T} must be 3 or 4.
  tileAWidth ${v} must be divisible by workgroupSize[0]${r[0]}. tileInner ${p} must be divisible by workgroupSize[1] ${r[1]}. colPerThread ${e[0]} must be 4.`);return`
var<workgroup> mm_Asub: array<array<vec${T}<${a}>, ${v/T}>, ${x}>;
var<workgroup> mm_Bsub: array<array<vec4<${a}>, ${_/e[0]}>, ${p}>;

const rowPerThread = ${e[1]};
const colPerThread = ${e[0]};
const innerElementSize = ${T};
const tileInner = ${p};

@compute @workgroup_size(${r[0]}, ${r[1]}, ${r[2]})
fn main(@builtin(local_invocation_id) localId : vec3<u32>,
        @builtin(global_invocation_id) globalId : vec3<u32>,
        @builtin(workgroup_id) workgroupId : vec3<u32>) {
  let localRow = i32(localId.y);
  let tileRow = localRow * rowPerThread;
  let tileCol = i32(localId.x);

  let globalRow =i32(globalId.y) * rowPerThread;
  let globalCol = i32(globalId.x);
  let batch = ${d?"0":"i32(globalId.z)"};
  ${s?`let batchIndices = ${s.offsetToIndices("u32(batch)")};`:""}
  let globalRowStart = i32(workgroupId.y) * ${m};

  let num_tiles = ${d?`${Math.ceil(g/p)}`:"(uniforms.dim_inner - 1) / tileInner + 1"};
  var kStart = ${d?`i32(globalId.z) * ${g}`:"0"};

  var acc: array<vec4<${a}>, rowPerThread>;

  // Loop over shared dimension.
  let tileRowB = localRow * ${C};
  for (var t = 0; t < num_tiles; t = t + 1) {
      // Load one tile of A into local memory.
      for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
          let inputRow = tileRow + innerRow;
          let inputCol = tileCol;
          ${ky(o,s)}
      }

      // Load one tile of B into local memory.
      for (var innerRow = 0; innerRow < ${C}; innerRow = innerRow + 1) {
          let inputRow = tileRowB + innerRow;
          let inputCol = tileCol;
          mm_Bsub[inputRow][inputCol] = mm_readB(batch, kStart + inputRow, globalCol${s?", batchIndices":""});
      }
      kStart = kStart + tileInner;
      workgroupBarrier();

      // Compute acc values for a single thread.
      for (var k = 0; k < tileInner / innerElementSize; k = k + 1) {
          let BCached0 = mm_Bsub[k * innerElementSize][tileCol];
          let BCached1 = mm_Bsub[k * innerElementSize + 1][tileCol];
          let BCached2 = mm_Bsub[k * innerElementSize + 2][tileCol];
          ${T===3?"":"let BCached3 = mm_Bsub[k * innerElementSize + 3][tileCol];"}

          ${Ey(o,T)}
      }

      workgroupBarrier();
  }

  for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
      mm_write(batch, globalRow + innerRow, globalCol, acc[innerRow]);
  }
}`},Cd=(e,r)=>e?`
            mm_Asub[inputRow][inputCol] = mm_readA(batch,
              kStart + inputRow,
              globalRowStart + inputCol${r?", batchIndices":""});
            `:`
            mm_Asub[inputRow][inputCol] = mm_readA(batch,
              globalRowStart + inputRow,
              kStart + inputCol${r?", batchIndices":""});
            `,Iy=e=>e?"let ACached = mm_Asub[k][tileRow + innerRow];":"let ACached = mm_Asub[tileRow + innerRow][k];",fp=(e,r,a="f32",s,o=!1,p=32,d=!1,g=32,m=!1)=>{let _=e[1]*r[1],v=e[0]*r[0],x=o?_:p,T=o?p:_;if(!(T%r[1]===0&&x%r[0]===0&&p%r[1]===0))throw new Error(`tileAHight ${T} must be divisible by workgroupSize[1]${r[1]}, tileAWidth ${x} must be divisible by workgroupSize[0]${r[0]}, tileInner ${p} must be divisible by workgroupSize[1]${r[1]}`);let C=T/r[1],A=x/r[0],R=p/r[1],H=m?`
    let localRow = i32(localId.y);
    let localCol = i32(localId.x);
    let globalRowStart = i32(workgroupId.y) * ${_};
    let globalColStart = i32(workgroupId.x) * ${v};

    // Loop over shared dimension.
    for (var t = 0; t < num_tiles; t = t + 1) {
      // Load one tile of A into local memory.
      for (var inputRow = localRow; inputRow < ${T}; inputRow = inputRow + ${r[1]}) {
        for (var inputCol = localCol; inputCol < ${x}; inputCol = inputCol + ${r[0]}) {
          ${Cd(o,s)}
        }
      }
      // Load one tile of B into local memory.
      for (var inputRow = localRow; inputRow < ${p}; inputRow = inputRow + ${r[1]}) {
            for (var inputCol = localCol; inputCol < ${v}; inputCol = inputCol + ${r[0]}) {
          mm_Bsub[inputRow][inputCol] = mm_readB(batch,
            kStart + inputRow,
            globalColStart + inputCol${s?", batchIndices":""});
        }
      }
      kStart = kStart + tileInner;
      workgroupBarrier();

      // Compute acc values for a single thread.
      var BCached : array<${a}, colPerThread>;
      for (var k = 0; k < tileInner; k = k + 1) {
        for (var inner = 0; inner < colPerThread; inner = inner + 1) {
          BCached[inner] = mm_Bsub[k][localCol + inner * ${r[0]}];
        }
        for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
          let ACached = ${o?`mm_Asub[k][localRow + innerRow * ${r[1]}];`:`mm_Asub[localRow + innerRow * ${r[1]}][k];`}
          for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
            acc[innerRow][innerCol] = acc[innerRow][innerCol] +
                ACached * BCached[innerCol];
          }
        }
      }
      workgroupBarrier();
    }
    for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
      let gRow = globalRowStart + localRow + innerRow * ${r[1]};
      for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
        let gCol = globalColStart + localCol + innerCol * ${r[0]};
        mm_write(batch, gRow, gCol, acc[innerRow][innerCol]);
      }
    }
    `:`
let tileRow = i32(localId.y) * rowPerThread;
let tileCol = i32(localId.x) * colPerThread;

let globalRow = i32(globalId.y) * rowPerThread;
let globalCol = i32(globalId.x) * colPerThread;
let globalRowStart = i32(workgroupId.y) * ${_};

let tileRowA = i32(localId.y) * ${C};
let tileColA = i32(localId.x) * ${A};
let tileRowB = i32(localId.y) * ${R};
// Loop over shared dimension.
for (var t = 0; t < num_tiles; t = t + 1) {
  // Load one tile of A into local memory.
  for (var innerRow = 0; innerRow < ${C}; innerRow = innerRow + 1) {
    for (var innerCol = 0; innerCol < ${A}; innerCol = innerCol + 1) {
      let inputRow = tileRowA + innerRow;
      let inputCol = tileColA + innerCol;
      ${Cd(o,s)}
    }
  }

  // Load one tile of B into local memory.
  for (var innerRow = 0; innerRow < ${R}; innerRow = innerRow + 1) {
    for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
      let inputRow = tileRowB + innerRow;
      let inputCol = tileCol + innerCol;
      mm_Bsub[inputRow][inputCol] = mm_readB(batch,
        kStart + inputRow,
        globalCol + innerCol${s?", batchIndices":""});
    }
  }
  kStart = kStart + tileInner;
  workgroupBarrier();

  // Compute acc values for a single thread.
  var BCached : array<${a}, colPerThread>;
  for (var k = 0; k < tileInner; k = k + 1) {
    for (var inner = 0; inner < colPerThread; inner = inner + 1) {
      BCached[inner] = mm_Bsub[k][tileCol + inner];
    }

    for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
      ${Iy(o)}
      for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
        acc[innerRow][innerCol] = acc[innerRow][innerCol] + ACached * BCached[innerCol];
      }
    }
  }

  workgroupBarrier();
}

for (var innerRow = 0; innerRow < rowPerThread; innerRow = innerRow + 1) {
  for (var innerCol = 0; innerCol < colPerThread; innerCol = innerCol + 1) {
    mm_write(batch, globalRow + innerRow, globalCol + innerCol,
        acc[innerRow][innerCol]);
  }
}
`;return`
  var<workgroup> mm_Asub : array<array<${a}, ${x}>, ${T}>;
  var<workgroup> mm_Bsub : array<array<${a}, ${v}>, ${p}>;
  const rowPerThread = ${e[1]};
  const colPerThread = ${e[0]};
  const tileInner = ${p};

@compute @workgroup_size(${r[0]}, ${r[1]}, ${r[2]})
fn main(@builtin(local_invocation_id) localId : vec3<u32>,
        @builtin(global_invocation_id) globalId : vec3<u32>,
        @builtin(workgroup_id) workgroupId : vec3<u32>) {
    let batch = ${d?"0":"i32(globalId.z)"};
    ${s?`let batchIndices = ${s.offsetToIndices("u32(batch)")};`:""}
    let num_tiles = ${d?`${Math.ceil(g/p)}`:"(uniforms.dim_inner - 1) / tileInner + 1"};
    var kStart = ${d?`i32(globalId.z) * ${g}`:"0"};

    var acc : array<array<${a}, colPerThread>, rowPerThread>;
    ${H}
  }
`},zy=(e,r,a,s,o=!1)=>{let[p,d,g,m]=s,_=yr(s[0].type.tensor);return`
    fn mm_readA(batch: i32, row: i32, colIn: i32, batchIndices: ${p.type.indices}) -> ${Tr(e,_)} {
      var value = ${Tr(e,_)}(0.0);
      let col = colIn * ${e};
      if(row < uniforms.dim_a_outer && col < uniforms.dim_inner)
      {
        var aIndices: ${d.type.indices};
        ${Wo("aIndices",d,d.rank-2,p.rank,"batchIndices")}
        ${d.indicesSet("aIndices",d.rank-2,"u32(row)")}
        ${d.indicesSet("aIndices",d.rank-1,"u32(colIn)")}
        value = ${d.getByIndices("aIndices")};
      }
      return value;
    }

    fn mm_readB(batch: i32, row: i32, colIn: i32, batchIndices: ${p.type.indices}) -> ${Tr(e,_)} {
      var value = ${Tr(e,_)}(0.0);
      let col = colIn * ${e};
      if(row < uniforms.dim_inner && col < uniforms.dim_b_outer)
      {
        var bIndices: ${g.type.indices};
        ${Wo("bIndices",g,g.rank-2,p.rank,"batchIndices")}
        ${g.indicesSet("bIndices",g.rank-2,"u32(row)")}
        ${g.indicesSet("bIndices",g.rank-1,"u32(colIn)")}
        value = ${g.getByIndices("bIndices")};
      }
      return value;
    }

    fn mm_write(batch: i32, row: i32, colIn: i32, valueIn: ${Tr(e,_)}) {
      let col = colIn * ${e};
      if (row < uniforms.dim_a_outer && col < uniforms.dim_b_outer) {
        var value = valueIn;
        let coords = vec3<i32>(batch, row, colIn);
        ${r?`value = value + ${o?"bias[colIn]":`${Tr(e,_)}(bias[row])`};`:""}
        ${a}
        ${m.setByIndices("vec3<u32>(coords)","value")}
      }
    }
    `},sl=(e,r,a,s,o=!1,p)=>{let d=e[0].dims,g=e[1].dims,m=d.slice(0,-2),_=g.slice(0,-2),v=s?s.slice(0,-2):a.slice(0,-2),x=ge.size(v),T=d[d.length-2],C=d[d.length-1],A=g[g.length-1],R=C%4===0&&A%4===0,H=T<=8?[4,1,1]:[4,4,1],U=[8,8,1],P=[Math.ceil(A/U[0]/H[0]),Math.ceil(T/U[1]/H[1]),Math.ceil(x/U[2]/H[2])],F=R?4:1,G=[...m,T,C/F],K=G.length,ee=[..._,C,A/F],ae=ee.length,B=[x,T,A/F],me=[{type:6,data:T},{type:6,data:A},{type:6,data:C}];Wn(r,me),me.push(...Je(v,G,ee));let _e=["rank","rank"],Re=e.length>2;Re&&(me.push(...Je(e[2].dims)),_e.push("rank")),me.push(...Je(B));let Ue=Me=>{let pe=v.length,qe=Bp("batchDims",e[0].dataType,pe,1),Ve=yr(e[0].dataType),ze=$e("a",e[0].dataType,K,F),ht=$e("b",e[1].dataType,ae,F),Ce=je("result",e[0].dataType,B.length,F),nt=[ze,ht];if(Re){let $t=o?F:1;nt.push($e("bias",e[2].dataType,e[2].dims.length,$t))}let Te=[{name:"dim_a_outer",type:"i32"},{name:"dim_b_outer",type:"i32"},{name:"dim_inner",type:"i32"}];Gn(r,Te);let Be=yr(Ce.type.tensor),We=Vn(r,Ce.type.value,Be),Ie=zy(F,Re,We,[qe,ze,ht,Ce],o);return`
  ${Me.registerUniforms(Te).registerInternalVariables(qe).declareVariables(...nt,Ce)}
  ${Ie}
  ${R?hp(H,U,Ve,qe):fp(H,U,Ve,qe)}
                   `};return{name:"MatMul",shaderCache:{hint:`${H};${r.activation};${R};${o}`,inputDependencies:_e},getRunData:()=>({outputs:[{dims:p?p(a):a,dataType:e[0].dataType}],dispatchGroup:{x:P[0],y:P[1],z:P[2]},programUniforms:me}),getShaderSource:Ue}}}),Cy,Qb,u1=Ee(()=>{ut(),Ta(),ft(),Hn(),Up(),o1(),Vp(),Cy=(e,r,a,s,o=!1,p,d=4,g=4,m=4,_="f32")=>{let v=me=>{switch(me){case 1:return"resData = x[xIndex];";case 3:return`resData = vec3<${_}>(x[xIndex], x[xIndex + 1], x[xIndex + 2]);`;case 4:return"resData = x[xIndex / 4];";default:throw new Error(`innerElementSize ${me} is not supported.`)}},x=me=>{switch(me){case 1:return"return w[row * i32(uniforms.w_shape[3]) + colIn];";case 4:return"return w[row * i32(uniforms.w_shape[3]) / 4 + colIn];";default:throw new Error(`innerElementSize ${me} is not supported.`)}},T=e?`
    let coord = vec4<i32>(batch, xRow, xCol, xCh);
    `:`
    let coord = vec4<i32>(batch, xCh, xRow, xCol);
    `,C=e?`
    let coords = vec4<i32>(
      batch,
      row / outWidth,
      row % outWidth,
      col);
    `:`
    let coords = vec4<i32>(
      batch,
      row,
      col / outWidth,
      col % outWidth);
    `,A=e?"i32(uniforms.x_shape[1])":"i32(uniforms.x_shape[2])",R=e?"i32(uniforms.x_shape[2])":"i32(uniforms.x_shape[3])",H=e?"row":"col",U=e?"col":"row",P=`
    let inChannels = i32(uniforms.w_shape[2]);
    let outWidth = ${e?"i32(uniforms.result_shape[2])":"i32(uniforms.result_shape[3])"};
    let outRow = ${H} / outWidth;
    let outCol = ${H} % outWidth;

    let WRow = ${U} / (i32(uniforms.w_shape[1]) * inChannels);
    let WCol = ${U} / inChannels % i32(uniforms.w_shape[1]);
    let xRow = outRow * uniforms.stride[0] + uniforms.dilation[0] * WRow - uniforms.pad[0];
    let xCol = outCol * uniforms.stride[1] + uniforms.dilation[1] * WCol - uniforms.pad[1];
    let xCh = ${U} % inChannels;
    var resData = ${Tr(d,_)}(0.0);
    // The bounds checking is always needed since we use it to pad zero for
    // the 'same' padding type.
    if (xRow >= 0 && xRow < ${A} && xCol >= 0 && xCol < ${R}) {
      ${T}
      let xIndex = getIndexFromCoords4D(coord, vec4<i32>(uniforms.x_shape));
      ${v(d)}
    }
    return resData;`,F=e?r&&s?`
    let col = colIn * ${d};
    ${P}`:`
    let col = colIn * ${d};
    if (row < uniforms.dim_a_outer && col < uniforms.dim_inner) {
      ${P}
    }
    return ${Tr(d,_)}(0.0);`:s&&a?`
    let col = colIn * ${d};
    ${P}`:`
    let col = colIn * ${d};
    if (row < uniforms.dim_inner && col < uniforms.dim_b_outer) {
      ${P}
    }
    return ${Tr(d,_)}(0.0);`,G=e?s&&a?x(g):`
    let col = colIn * ${g};
    if (row < uniforms.dim_inner && col < uniforms.dim_b_outer) {
      ${x(g)}
    }
    return ${Tr(g,_)}(0.0);`:`
    let col = colIn * ${g};
    if (row < uniforms.dim_inner && col < uniforms.dim_a_outer) {
      ${x(g)}
    }
    return ${Tr(g,_)}(0.0);`,K=Tr(m,_),ee=Tr(e?d:g,_),ae=Tr(e?g:d,_),B=Vn(p,K,_);return`
    fn mm_readA(batch: i32, row : i32, colIn : i32) -> ${ee} {
      ${e?F:G}
    }

    fn mm_readB(batch: i32, row : i32, colIn : i32) -> ${ae} {
      ${e?G:F}
    }

    fn mm_write(batch: i32, row : i32, colIn : i32, valueIn : ${K}) {
      let col = colIn * ${m};
      if (row < uniforms.dim_a_outer && col < uniforms.dim_b_outer)
      {
      var value = valueIn;
      let outWidth = ${e?"i32(uniforms.result_shape[2])":"i32(uniforms.result_shape[3])"};
      ${C}
      ${Kb(o)}
      ${B}
      setOutputAtCoords(coords[0], coords[1], coords[2], coords[3], value);
      }
    }`},Qb=(e,r,a,s,o,p,d,g,m)=>{let _=r.format==="NHWC",v=_?e[0].dims[3]:e[0].dims[1],x=a[0],T=_?a[2]:a[3],C=_?a[1]:a[2],A=_?a[3]:a[1],R=_&&(v%4===0||v%3===0)&&A%4===0,H=_?A:T*C,U=_?T*C:A,P=[8,8,1],F=s<=8?[4,1,1]:[4,4,1],G=[Math.ceil(H/P[0]/F[0]),Math.ceil(U/P[1]/F[1]),Math.ceil(x/P[2]/F[2])];It("verbose",()=>`[conv2d_mm_webgpu] dispatch = ${G}`);let K=R?_&&v%4!==0?3:4:1,ee=P[1]*F[1],ae=P[0]*F[0],B=Math.max(P[0]*K,P[1]),me=s%ee===0,_e=o%ae===0,Re=p%B===0,Ue=R?[K,4,4]:[1,1,1],Me=[{type:6,data:s},{type:6,data:o},{type:6,data:p},{type:6,data:[r.pads[0],r.pads[1]]},{type:6,data:r.strides},{type:6,data:r.dilations}];Wn(r,Me),Me.push(...Je(e[0].dims,e[1].dims));let pe=["rank","rank"];d&&(Me.push(...Je(e[2].dims)),pe.push("rank")),Me.push(...Je(a));let qe=Ve=>{let ze=[{name:"dim_a_outer",type:"i32"},{name:"dim_b_outer",type:"i32"},{name:"dim_inner",type:"i32"},{name:"pad",type:"i32",length:2},{name:"stride",type:"i32",length:2},{name:"dilation",type:"i32",length:2}];Gn(r,ze);let ht=R?4:1,Ce=yr(e[0].dataType),nt=`
      fn setOutputAtIndex(flatIndex : i32, value : ${R?`vec4<${Ce}>`:Ce}) {
        result[flatIndex] = ${R?`vec4<${Ce}>`:Ce}(value);
      }
      fn setOutputAtCoords(d0 : i32, d1 : i32, d2 : i32, d3 : i32, value : ${R?`vec4<${Ce}>`:Ce}) {
        let flatIndex = getOutputIndexFromCoords(vec4<i32>(d0, d1, d2, d3));
        setOutputAtIndex(flatIndex ${R?"/ 4":""}, value);
      }`,Te=$e("x",e[0].dataType,e[0].dims.length,K===3?1:K),Be=$e("w",e[1].dataType,e[1].dims.length,ht),We=[Te,Be],Ie=je("result",e[0].dataType,a.length,ht);if(d){let $t=$e("bias",e[2].dataType,e[2].dims.length,ht);We.push($t),nt+=`
        fn getBiasByOutputCoords(coords : vec4<i32>) -> ${R?`vec4<${Ce}>`:Ce} {
          return bias[coords.${_?"w":"y"}${R?"/ 4":""}];
        }`}return`
        ${Zb("uniforms.result_strides")}
        //struct Uniforms { xShape : vec4<i32>, wShape : vec4<i32>, outShape : vec4<i32>,
        //  outShapeStrides: vec3<i32>, filterDims : vec2<i32>, pad : vec2<i32>, stride : vec2<i32>,
        //  dilation : vec2<i32>, dimAOuter : i32, dimBOuter : i32, dimInner : i32 };
        ${Ve.registerUniforms(ze).declareVariables(...We,Ie)}
        ${nt}
        ${Cy(_,me,_e,Re,d,r,Ue[0],Ue[1],Ue[2],Ce)}
        ${R?hp(F,P,Ce,void 0,!_,B):fp(F,P,Ce,void 0,!_,B,!1,void 0,g)}`};return{name:"Conv2DMatMul",shaderCache:{hint:`${r.cacheKey};${K};${R};${me};${_e};${Re};${ee};${ae};${B}`,inputDependencies:pe},getRunData:()=>({outputs:[{dims:m?m(a):a,dataType:e[0].dataType}],dispatchGroup:{x:G[0],y:G[1],z:G[2]},programUniforms:Me}),getShaderSource:qe}}}),Ay,Ad,Oo,Oy,Od,Ry,Xb,Yb,l1=Ee(()=>{ut(),Ta(),ct(),ft(),Hn(),Up(),Ay=e=>{let r=1;for(let a=0;a<e.length;a++)r*=e[a];return r},Ad=e=>typeof e=="number"?[e,e,e]:e,Oo=(e,r)=>r<=1?e:e+(e-1)*(r-1),Oy=(e,r,a,s=1)=>{let o=Oo(r,s);return Math.floor((e[0]*(a-1)-a+o)/2)},Od=(e,r,a,s,o)=>{o==null&&(o=Oy(e,r[0],s[0]));let p=[0,0,0,a];for(let d=0;d<3;d++)e[d]+2*o>=r[d]&&(p[d]=Math.trunc((e[d]-r[d]+2*o)/s[d]+1));return p},Ry=(e,r,a,s,o,p,d,g,m,_)=>{let v,x,T,C;if(e==="VALID"&&(e=0),typeof e=="number"){v={top:e,bottom:e,left:e,right:e,front:e,back:e};let A=Od([r,a,s,1],[g,m,_],1,[o,p,d],e);x=A[0],T=A[1],C=A[2]}else if(Array.isArray(e)){if(!e.every((R,H,U)=>R===U[0]))throw Error(`Unsupported padding parameter: ${e}`);v={top:e[0],bottom:e[1],left:e[2],right:e[3],front:e[4],back:e[5]};let A=Od([r,a,s,1],[g,m,_],1,[o,p,d],e[0]);x=A[0],T=A[1],C=A[2]}else if(e==="SAME_UPPER"){x=Math.ceil(r/o),T=Math.ceil(a/p),C=Math.ceil(s/d);let A=(x-1)*o+g-r,R=(T-1)*p+m-a,H=(C-1)*d+_-s,U=Math.floor(A/2),P=A-U,F=Math.floor(R/2),G=R-F,K=Math.floor(H/2),ee=H-K;v={top:F,bottom:G,left:K,right:ee,front:U,back:P}}else throw Error(`Unknown padding parameter: ${e}`);return{padInfo:v,outDepth:x,outHeight:T,outWidth:C}},Xb=(e,r,a,s,o,p=!1,d="channelsLast")=>{let g,m,_,v,x;if(d==="channelsLast")[g,m,_,v,x]=e;else if(d==="channelsFirst")[g,x,m,_,v]=e;else throw new Error(`Unknown dataFormat ${d}`);let[T,,C,A,R]=r,[H,U,P]=Ad(a),[F,G,K]=Ad(s),ee=Oo(C,F),ae=Oo(A,G),B=Oo(R,K),{padInfo:me,outDepth:_e,outHeight:Re,outWidth:Ue}=Ry(o,m,_,v,H,U,P,ee,ae,B),Me=p?T*x:T,pe=[0,0,0,0,0];return d==="channelsFirst"?pe=[g,Me,_e,Re,Ue]:d==="channelsLast"&&(pe=[g,_e,Re,Ue,Me]),{batchSize:g,dataFormat:d,inDepth:m,inHeight:_,inWidth:v,inChannels:x,outDepth:_e,outHeight:Re,outWidth:Ue,outChannels:Me,padInfo:me,strideDepth:H,strideHeight:U,strideWidth:P,filterDepth:C,filterHeight:A,filterWidth:R,effectiveFilterDepth:ee,effectiveFilterHeight:ae,effectiveFilterWidth:B,dilationDepth:F,dilationHeight:G,dilationWidth:K,inShape:e,outShape:pe,filterShape:r}},Yb=(e,r,a,s,o,p)=>{let d=p==="channelsLast";d?e[0].dims[3]:e[0].dims[1];let g=[64,1,1],m={x:a.map((H,U)=>U)},_=[Math.ceil(Ay(m.x.map(H=>a[H]))/g[0]),1,1];It("verbose",()=>`[conv3d_naive_webgpu] dispatch = ${_}`);let v=1,x=ge.size(a),T=[{type:12,data:x},{type:12,data:s},{type:12,data:o},{type:12,data:r.strides},{type:12,data:r.dilations}];Wn(r,T),T.push(...Je(e[0].dims,e[1].dims));let C=["rank","rank"],A=e.length===3;A&&(T.push(...Je(e[2].dims)),C.push("rank")),T.push(...Je(a));let R=H=>{let U=[{name:"output_size",type:"u32"},{name:"filter_dims",type:"u32",length:s.length},{name:"pads",type:"u32",length:o.length},{name:"strides",type:"u32",length:r.strides.length},{name:"dilations",type:"u32",length:r.dilations.length}];Gn(r,U);let P=1,F=yr(e[0].dataType),G=$e("x",e[0].dataType,e[0].dims.length,v),K=$e("W",e[1].dataType,e[1].dims.length,P),ee=[G,K],ae=je("result",e[0].dataType,a.length,P),B="";if(A){let Re=$e("bias",e[2].dataType,e[2].dims.length,P);ee.push(Re),B+=`
        fn getBiasByOutputCoords(coords : array<u32, 5>) -> ${F} {
          return bias[${d?Qe("coords",4,5):Qe("coords",1,5)}];
        }`}let me=Tr(v,F),_e=Vn(r,me,F);return`
            ${B}
            fn getX(d0 : u32, d1 : u32, d2 : u32, d3 : u32, d4 : u32) -> f32 {
              let aIndices = array<u32, 5>(d0, d1, d2, d3, d4);
              return ${G.getByIndices("aIndices")};
            }
            fn getW(d0 : u32, d1 : u32, d2 : u32, d3 : u32, d4 : u32) -> f32 {
              let aIndices = array<u32, 5>(d0, d1, d2, d3, d4);
              return ${K.getByIndices("aIndices")};
            }
          ${H.registerUniforms(U).declareVariables(...ee,ae)}
          ${H.mainStart()}
          ${H.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
              let coords = ${ae.offsetToIndices("global_idx")};
              let batch = ${Qe("coords",0,G.rank)};
              let d2 = ${d?Qe("coords",G.rank-1,G.rank):Qe("coords",1,G.rank)};
              let xFRCCorner = vec3<u32>(${d?Qe("coords",1,G.rank):Qe("coords",2,G.rank)},
              ${d?Qe("coords",2,G.rank):Qe("coords",3,G.rank)},
              ${d?Qe("coords",3,G.rank):Qe("coords",4,G.rank)}) * uniforms.strides - uniforms.pads;
              let xFCorner = xFRCCorner.x;
              let xRCorner = xFRCCorner.y;
              let xCCorner = xFRCCorner.z;
              let xShapeY = ${d?Qe("uniforms.x_shape",1,G.rank):Qe("uniforms.x_shape",2,G.rank)};
              let xShapeZ = ${d?Qe("uniforms.x_shape",2,G.rank):Qe("uniforms.x_shape",3,G.rank)};
              let xShapeW = ${d?Qe("uniforms.x_shape",3,G.rank):Qe("uniforms.x_shape",4,G.rank)};
              let xShapeU = ${d?Qe("uniforms.x_shape",4,G.rank):Qe("uniforms.x_shape",1,G.rank)};
              let inputDepthNearestVec4 = (xShapeU / 4) * 4;
              let inputDepthVec4Remainder = xShapeU % 4;

              var value = 0.0;
              for (var wF = 0u; wF < uniforms.filter_dims[0]; wF++) {
                let xF = xFCorner + wF * uniforms.dilations[0];
                if (xF < 0 || xF >= xShapeY) {
                  continue;
                }

                for (var wR = 0u; wR < uniforms.filter_dims[1]; wR++) {
                  let xR = xRCorner + wR * uniforms.dilations[1];
                  if (xR < 0 || xR >= xShapeZ) {
                    continue;
                  }

                  for (var wC = 0u; wC < uniforms.filter_dims[2]; wC++) {
                    let xC = xCCorner + wC * uniforms.dilations[2];
                    if (xC < 0 || xC >= xShapeW) {
                      continue;
                    }

                    for (var d1 = 0u; d1 < inputDepthNearestVec4; d1 += 4) {
                      ${d?`let xValues = vec4<f32>(
                               getX(batch, xF, xR, xC, d1),
                               getX(batch, xF, xR, xC, d1 + 1),
                               getX(batch, xF, xR, xC, d1 + 2),
                               getX(batch, xF, xR, xC, d1 + 3));
                            `:`let xValues = vec4<f32>(
                               getX(batch, d1, xF, xR, xC),
                               getX(batch, d1 + 1, xF, xR, xC),
                               getX(batch, d1 + 2, xF, xR, xC),
                               getX(batch, d1 + 3, xF, xR, xC));
                            `}
                            let wValues = vec4<f32>(
                              getW(d2, d1, wF, wR, wC),
                              getW(d2, d1 + 1, wF, wR, wC),
                              getW(d2, d1 + 2, wF, wR, wC),
                              getW(d2, d1 + 3, wF, wR, wC));
                      value += dot(xValues, wValues);
                    }
                    if (inputDepthVec4Remainder == 1) {
                        ${d?`value += getX(batch, xF, xR, xC, inputDepthNearestVec4)
                          * getW(d2, inputDepthNearestVec4, wF, wR, wC);`:`value += getX(batch, inputDepthNearestVec4, xF, xR, xC)
                          * getW(d2, inputDepthNearestVec4, wF, wR, wC);`}
                    } else if (inputDepthVec4Remainder == 2) {
                      ${d?`let xValues = vec2<f32>(
                        getX(batch, xF, xR, xC, inputDepthNearestVec4),
                        getX(batch, xF, xR, xC, inputDepthNearestVec4 + 1));
                      `:`let xValues = vec2<f32>(
                        getX(batch, inputDepthNearestVec4, xF, xR, xC),
                        getX(batch, inputDepthNearestVec4 + 1, xF, xR, xC));
                    `}
                    let wValues = vec2<f32>(
                      getW(d2, inputDepthNearestVec4, wF, wR, wC),
                      getW(d2, inputDepthNearestVec4 + 1, wF, wR, wC));
                      value += dot(xValues, wValues);
                    } else if (inputDepthVec4Remainder == 3) {
                      ${d?`let xValues = vec3<f32>(
                        getX(batch, xF, xR, xC, inputDepthNearestVec4),
                        getX(batch, xF, xR, xC, inputDepthNearestVec4 + 1),
                        getX(batch, xF, xR, xC, inputDepthNearestVec4 + 2));
                      `:`let xValues = vec3<f32>(
                        getX(batch, inputDepthNearestVec4, xF, xR, xC),
                        getX(batch, inputDepthNearestVec4 + 1, xF, xR, xC),
                        getX(batch, inputDepthNearestVec4 + 2, xF, xR, xC));
                    `}
                    let wValues = vec3<f32>(
                      getW(d2, inputDepthNearestVec4, wF, wR, wC),
                      getW(d2, inputDepthNearestVec4 + 1, wF, wR, wC),
                      getW(d2, inputDepthNearestVec4 + 2, wF, wR, wC));
                      value += dot(xValues, wValues);
                    }
                  }
                }
              }
              ${A?"value = value + getBiasByOutputCoords(coords)":""};
              ${_e}
              result[global_idx] = f32(value);
          }`};return{name:"Conv3DNaive",shaderCache:{hint:`${r.cacheKey};${d};${v};${A}`,inputDependencies:C},getRunData:()=>({outputs:[{dims:a,dataType:e[0].dataType}],dispatchGroup:{x:_[0],y:_[1],z:_[2]},programUniforms:T}),getShaderSource:R}}}),Jb,e$,d1=Ee(()=>{ut(),ct(),ft(),Hn(),Jb=(e,r,a,s)=>{let o=e.length>2,p=o?"value += b[output_channel];":"",d=e[0].dims,g=e[1].dims,m=r.format==="NHWC",_=m?a[3]:a[1],v=_/r.group,x=m&&v>=4?Yt(_):1,T=ge.size(a)/x,C=[{type:12,data:T},{type:12,data:r.dilations},{type:12,data:[r.strides[0],r.strides[1]]},{type:12,data:[r.pads[0],r.pads[1]]},{type:12,data:v}];Wn(r,C),C.push(...Je(d,[g[0],g[1],g[2],g[3]/x]));let A=o?["rank","rank","rank"]:["rank","rank"];C.push(...Je([a[0],a[1],a[2],a[3]/x]));let R=H=>{let U=je("output",e[0].dataType,a.length,x),P=yr(U.type.tensor),F=Vn(r,U.type.value,P),G=$e("x",e[0].dataType,d.length),K=$e("w",e[1].dataType,g.length,x),ee=[G,K];o&&ee.push($e("b",e[2].dataType,e[2].dims,x));let ae=[{name:"output_size",type:"u32"},{name:"dilations",type:"u32",length:r.dilations.length},{name:"strides",type:"u32",length:2},{name:"pads",type:"u32",length:2},{name:"output_channels_per_group",type:"u32"}];Gn(r,ae);let B=m?`
      for (var wHeight: u32 = 0u; wHeight < uniforms.w_shape[0]; wHeight++) {
        let xHeight = xRCCorner.x + wHeight * uniforms.dilations[0];

        if (xHeight < 0u || xHeight >= uniforms.x_shape[1]) {
          continue;
        }

        for (var wWidth: u32 = 0u; wWidth < uniforms.w_shape[1]; wWidth++) {
          let xWidth = xRCCorner.y + wWidth * uniforms.dilations[1];
          if (xWidth < 0u || xWidth >= uniforms.x_shape[2]) {
            continue;
          }

          for (var wInChannel: u32 = 0u; wInChannel < uniforms.w_shape[2]; wInChannel++) {
            let input_channel = in_channel_offset + wInChannel;
            let xVal = ${G.get("batch","xHeight","xWidth","input_channel")};
            let wVal = ${K.get("wHeight","wWidth","wInChannel","output_channel")};
            value += xVal * wVal;
          }
        }
      }
      `:`
      for (var wInChannel: u32 = 0u; wInChannel < uniforms.w_shape[1]; wInChannel++) {
        let input_channel = in_channel_offset + wInChannel;
        for (var wHeight: u32 = 0u; wHeight < uniforms.w_shape[2]; wHeight++) {
          let xHeight = xRCCorner.x + wHeight * uniforms.dilations[0];

          if (xHeight < 0u || xHeight >= uniforms.x_shape[2]) {
            continue;
          }

          for (var wWidth: u32 = 0u; wWidth < uniforms.w_shape[3]; wWidth++) {
            let xWidth = xRCCorner.y + wWidth * uniforms.dilations[1];
            if (xWidth < 0u || xWidth >= uniforms.x_shape[3]) {
              continue;
            }

            let xVal = ${G.get("batch","input_channel","xHeight","xWidth")};
            let wVal = ${K.get("output_channel","wInChannel","wHeight","wWidth")};
            value += xVal * wVal;
          }
        }
      }
      `;return`
  ${H.registerUniforms(ae).declareVariables(...ee,U)}

  ${H.mainStart()}
    ${H.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let outputIndices = ${U.offsetToIndices("global_idx")};
    let batch: u32 = outputIndices[0];
    let output_channel: u32 = outputIndices[${m?3:1}];
    let xRCCorner: vec2<u32> = vec2<u32>(outputIndices[${m?1:2}], outputIndices[${m?2:3}]) * uniforms.strides - uniforms.pads;
    let group_id: u32 = output_channel * ${x} / uniforms.output_channels_per_group;
    var in_channel_offset = group_id * uniforms.w_shape[${m?2:1}];

    var value: ${U.type.value} = ${U.type.value}(0);
    ${B}
    ${p}
    ${F}
    ${U.setByOffset("global_idx","value")}
  }`};return{name:"GroupedConv",shaderCache:{hint:`${r.cacheKey}_${x}`,inputDependencies:A},getRunData:()=>({outputs:[{dims:s?s(a):a,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(T/64)},programUniforms:C}),getShaderSource:R}},e$=(e,r,a,s)=>{let o=e.length>2,p=Yt(a[3]),d=Yt(a[2]),g=ge.size(a)/p/d,m=[e[0].dims[0],e[0].dims[1],e[0].dims[2],e[0].dims[3]/p],_=[e[1].dims[0],e[1].dims[1],e[1].dims[2],e[1].dims[3]/p],v=[a[0],a[1],a[2],a[3]/p],x=[{type:12,data:g},{type:6,data:[r.strides[0],r.strides[1]]},{type:6,data:[r.pads[0],r.pads[1]]}];Wn(r,x),x.push(...Je(m,_,v));let T=(d-1)*r.strides[1]+_[1],C=A=>{let R=je("output",e[0].dataType,v.length,p),H=yr(R.type.tensor),U=Vn(r,R.type.value,H),P=$e("x",e[0].dataType,m.length,p),F=$e("w",e[1].dataType,_.length,p),G=[P,F];o&&G.push($e("b",e[2].dataType,e[2].dims,p));let K=o?"value += b[output_channel];":"",ee=[{name:"output_size",type:"u32"},{name:"strides",type:"i32",length:2},{name:"pads",type:"i32",length:2}];return Gn(r,ee),`
  ${A.registerUniforms(ee).declareVariables(...G,R)}
  ${A.mainStart()}
    ${A.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let width0 = uniforms.output_shape[3];
    let output_channel = global_idx % width0;
    var index1 = global_idx / width0;
    let width1 = uniforms.output_shape[2] / ${d}u;
    let col = (index1 % width1) * ${d}u;
    index1 = index1 / width1;
    let row = index1 % uniforms.output_shape[1];
    let batch = index1 / uniforms.output_shape[1];

    let x_corner = vec2<i32>(i32(row), i32(col)) * uniforms.strides - uniforms.pads;

    var x_vals: array<${P.type.value}, ${T}>;
    var values: array<${R.type.value}, ${d}>;
    let input_channel = output_channel;
    // Use constant instead of uniform can give better performance for w's height/width.
    for (var w_height: u32 = 0u; w_height < ${_[0]}; w_height++) {
      let x_height = x_corner.x + i32(w_height);
      if (x_height >= 0 && u32(x_height) < uniforms.x_shape[1]) {
        for (var i = 0; i < ${T}; i++) {
          let x_width = x_corner.y + i;
          if (x_width >= 0 && u32(x_width) < uniforms.x_shape[2]) {
            x_vals[i] = ${P.get("batch","u32(x_height)","u32(x_width)","input_channel")};
          } else {
            x_vals[i] = ${P.type.value}(0);
          }
        }
        for (var w_width: u32 = 0u; w_width < ${_[1]}; w_width++) {
          let w_val = ${F.get("w_height","w_width","0","output_channel")};
          for (var i = 0u; i < ${d}u; i++) {
            values[i] = fma(x_vals[i * u32(uniforms.strides[1]) + w_width], w_val, values[i]);
          }
        }
      }
    }

    for (var i = 0u; i < ${d}u; i++) {
      var value = values[i];
      ${K}
      ${U}
      ${R.set("batch","row","col + i","output_channel","value")};
    }
  }`};return{name:"GroupedConv-Vectorize",shaderCache:{hint:`${r.cacheKey};${p};${d};${T};${_[0]};${_[1]}`,inputDependencies:o?["rank","rank","type"]:["rank","rank"]},getRunData:()=>({outputs:[{dims:s?s(a):a,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(g/64)},programUniforms:x}),getShaderSource:C}}}),By,Hu,My,ju,mp,Rd,Dy,Ny,gp,p1=Ee(()=>{ct(),u1(),l1(),Vp(),d1(),Hn(),qp(),nn(),By=(e,r,a,s,o,p)=>{let d=e[0],g=e.slice(p?1:2,p?3:4),m=g.length,_=r[0],v=r.slice(2).map((T,C)=>T+(T-1)*(a[C]-1)),x=g.map((T,C)=>T+s[C]+s[C+m]).map((T,C)=>Math.floor((T-v[C]+o[C])/o[C]));return x.splice(0,0,d),x.splice(p?3:1,0,_),x},Hu=[2,3,1,0],My=(e,r)=>{if(!e||e.length!==2&&e.length!==3)throw new Error("Conv requires 2 or 3 inputs");if(e[0].dims.length>5)throw new Error("greater than 5D is not supported");if(e[0].dims.length!==e[1].dims.length)throw new Error("filter does not have same dimension as input");let a=e[0].dims[r.format==="NHWC"?e[0].dims.length-1:1],s=e[1].dims[1]*r.group;if(a!==s)throw new Error("FILTER_IN_CHANNEL should be equal to DATA_CHANNEL");if(e.length===3&&(e[2].dims.length!==1||e[1].dims[0]!==e[2].dims[0]))throw new Error("invalid bias");let o=e[0].dims.length-2;if(r.dilations.length!==o)throw new Error(`dilations should be ${o}D`);if(r.strides.length!==o)throw new Error(`strides should be ${o}D`);if(r.pads.length!==o*2)throw new Error(`pads should be ${o*2}D`);if(r.kernelShape.length!==0&&r.kernelShape.length!==e[1].dims.length-2)throw new Error("invalid kernel shape")},ju=(e,r)=>{let a=e.kernelShape.slice();a.length<r[1].dims.length-2&&a.push(...Array(r[1].dims.length-2-a.length).fill(0));for(let p=2;p<r[1].dims.length;++p)a[p-2]===0&&(a[p-2]=r[1].dims[p]);let s=e.pads.slice();al.adjustPadsBasedOnAutoPad(r[0].dims,e.strides,e.dilations,a,s,e.format==="NHWC",e.autoPad);let o=Object.assign({},e);return Object.assign(o,{kernelShape:a,pads:s}),o},mp=e=>{let r=Pp(e),a=e.format,s=["NOTSET","VALID","SAME_UPPER","SAME_LOWER"][e.auto_pad],o=e.dilations,p=e.group,d=e.kernel_shape,g=e.pads,m=e.strides,_=e.w_is_const();return{autoPad:s,format:a,dilations:o,group:p,kernelShape:d,pads:g,strides:m,wIsConst:_,...r,cacheKey:`${e.format};${r.activation};`}},Rd=(e,r,a,s)=>{let o=a.format==="NHWC",p=By(r[0].dims,r[1].dims,a.dilations,a.pads,a.strides,o);if(a.group!==1){let ee=[r[0]];if(o){let ae=e.kernelCustomData.wT??e.compute(si(r[1],Hu),{inputs:[1],outputs:[a.wIsConst?-2:-1]})[0];a.wIsConst&&!e.kernelCustomData.wT&&(e.kernelCustomData.wT=ae),ee.push(ae)}else ee.push(r[1]);r.length===3&&ee.push(r[2]),!e.adapterInfo.isArchitecture("ampere")&&o&&r[1].dims[0]===a.group&&r[1].dims[1]===1&&a.dilations[0]===1&&a.dilations[1]===1?e.compute(e$(ee,a,p,s),{inputs:ee}):e.compute(Jb(ee,a,p,s),{inputs:ee});return}let d=r.length===3,g=r[0].dims[o?1:2],m=r[0].dims[o?2:3],_=r[0].dims[o?3:1],v=r[1].dims[2],x=r[1].dims[3],T=p[o?1:2],C=p[o?2:3],A=p[o?3:1],R=o&&v===g&&x===m&&a.pads[0]===0&&a.pads[1]===0;if(R||v===1&&x===1&&a.dilations[0]===1&&a.dilations[1]===1&&a.strides[0]===1&&a.strides[1]===1&&a.pads[0]===0&&a.pads[1]===0){let ee=p[0],ae,B,me,_e=[];if(o){let Me=e.kernelCustomData.wT??e.compute(si(r[1],Hu),{inputs:[1],outputs:[a.wIsConst?-2:-1]})[0];if(a.wIsConst&&!e.kernelCustomData.wT&&(e.kernelCustomData.wT=Me),R){let pe=g*m*_;ae=r[0].reshape([1,ee,pe]),B=Me.reshape([1,pe,A]),me=[1,ee,A]}else ae=r[0].reshape([ee,g*m,_]),B=Me.reshape([1,_,A]),me=[ee,T*C,A];_e.push(ae),_e.push(B)}else ae=r[0].reshape([ee,_,g*m]),B=r[1].reshape([1,A,_]),me=[ee,A,T*C],_e.push(B),_e.push(ae);d&&_e.push(r[2]);let Re=me[2],Ue=_e[0].dims[_e[0].dims.length-1];Re<8&&Ue<8?e.compute(Lp(_e,a,p,me,o,s),{inputs:_e}):e.compute(sl(_e,a,p,me,o,s),{inputs:_e});return}let H=!0,U=e.kernelCustomData.wT??e.compute(si(r[1],Hu),{inputs:[1],outputs:[a.wIsConst?-2:-1]})[0];a.wIsConst&&!e.kernelCustomData.wT&&(e.kernelCustomData.wT=U);let P=[r[0],U];d&&P.push(r[2]);let F=o?T*C:A,G=o?A:T*C,K=v*x*_;e.compute(Qb(P,a,p,F,G,K,d,H,s),{inputs:P})},Dy=(e,r)=>{let a=r.format==="NHWC",s=[e.inputs[0].reshape(a?[e.inputs[0].dims[0],1,e.inputs[0].dims[1],e.inputs[0].dims[2]]:[e.inputs[0].dims[0],e.inputs[0].dims[1],1,e.inputs[0].dims[2]]),e.inputs[1].reshape([e.inputs[1].dims[0],e.inputs[1].dims[1],1,e.inputs[1].dims[2]])];e.inputs.length===3&&s.push(e.inputs[2]);let o=[0,r.pads[0],0,r.pads[1]],p=[1].concat(r.strides),d=[1].concat(r.dilations),g=[1].concat(r.kernelShape),m=ju({...r,pads:o,strides:p,dilations:d,kernelShape:g},s);Rd(e,s,m,_=>a?[_[0],_[2],_[3]]:[_[0],_[1],_[3]])},Ny=(e,r,a)=>{let s=a.format==="NHWC"?"channelsLast":"channelsFirst",o=ju(a,r),p=a.autoPad==="NOTSET"?a.pads:a.autoPad,d=Xb(r[0].dims,r[1].dims,a.strides,a.dilations,p,!1,s);e.compute(Yb(r,o,d.outShape,[d.filterDepth,d.filterHeight,d.filterWidth],[d.padInfo.front,d.padInfo.top,d.padInfo.left],s))},gp=(e,r)=>{if(My(e.inputs,r),e.inputs[0].dims.length===3)Dy(e,r);else if(e.inputs[0].dims.length===5)Ny(e,e.inputs,r);else{let a=ju(r,e.inputs);Rd(e,e.inputs,a)}}}),t$,c1=Ee(()=>{ut(),Ta(),ct(),ft(),t$=(e,r,a)=>{let s=e.length>2,o=r.outputShape,p=r.format==="NHWC",d=r.group,g=e[1].dims,m=g[2]/d,_=g[3],v=p?Yt(m):1,x=p&&_===1&&m>=4,T=x?Math.floor(m/4)*4:Math.floor(m/v)*v,C=m-T,A=p?Yt(_):1,R=p?_===1?v:A:1,H=ge.size(o)/A,U=[Math.ceil(H/64),1,1];It("verbose",()=>`[conv2d_backprop_webgpu] dispatch = ${U}`);let P=["rank","rank"],F=[r.strides[0],r.strides[1]],G=[r.kernelShape[p?1:2],r.kernelShape[p?2:3]],K=[r.dilations[0],r.dilations[1]],ee=[G[0]+(r.dilations[0]<=1?0:(r.kernelShape[p?1:2]-1)*(r.dilations[0]-1)),G[1]+(r.dilations[1]<=1?0:(r.kernelShape[p?2:3]-1)*(r.dilations[1]-1))],ae=[ee[0]-1-Math.floor((r.pads[0]+r.pads[2])/2),ee[1]-1-Math.floor((r.pads[1]+r.pads[3])/2)],B=[{type:12,data:H},{type:12,data:F},{type:12,data:G},{type:12,data:K},{type:12,data:ee},{type:6,data:ae},{type:12,data:T},{type:12,data:m},{type:12,data:_},...Je(e[0].dims,e[1].dims)];s&&(B.push(...Je(e[2].dims)),P.push("rank")),B.push(...Je(o));let me=_e=>{let Re=[{name:"output_size",type:"u32"},{name:"strides",type:"u32",length:F.length},{name:"filter_dims",type:"u32",length:G.length},{name:"dilations",type:"u32",length:G.length},{name:"effective_filter_dims",type:"u32",length:ee.length},{name:"pads",type:"i32",length:ae.length},{name:"input_channels_per_group_int",type:"u32"},{name:"input_channels_per_group",type:"u32"},{name:"output_channels_per_group",type:"u32"}],Ue=yr(e[0].dataType),Me=p?1:2,pe=p?2:3,qe=p?3:1,Ve=$e("W",e[1].dataType,e[1].dims.length,R),ze=$e("Dy",e[0].dataType,e[0].dims.length,v),ht=[ze,Ve];s&&ht.push($e("bias",e[2].dataType,[o[qe]].length,A));let Ce=je("result",e[0].dataType,o.length,A),nt=()=>{let We="";if(x)v===4?We+=`
        let xValue = ${ze.getByOffset("x_offset")};
        let wValue = ${Ve.getByOffset("w_offset")};
        dotProd = dotProd + dot(xValue, wValue);
        x_offset += 1u;
        w_offset += 1u;`:v===2?We+=`
          dotProd = dotProd + dot(vec4<${Ue}>(${ze.getByOffset("x_offset")}, ${ze.getByOffset("x_offset + 1u")}), vec4<${Ue}>(${Ve.getByOffset("w_offset")}, ${Ve.getByOffset("w_offset + 1u")}));
          x_offset += 2u;
          w_offset += 2u;`:v===1&&(We+=`
          dotProd = dotProd + dot(vec4<${Ue}>(${ze.getByOffset("x_offset")}, ${ze.getByOffset("x_offset + 1u")}, ${ze.getByOffset("x_offset + 2u")}, ${ze.getByOffset("x_offset + 3u")}), vec4<${Ue}>(${Ve.getByOffset("w_offset")}, ${Ve.getByOffset("w_offset + 1u")}, ${Ve.getByOffset("w_offset + 2u")}, ${Ve.getByOffset("w_offset + 3u")}));
          x_offset += 4u;
          w_offset += 4u;`);else if(We+=`
                  let xValue = ${p?ze.getByOffset(`${ze.indicesToOffset(`${ze.type.indices}(batch, idyR, idyC, inputChannel)`)} / ${v}`):ze.get("batch","inputChannel","idyR","idyC")};
        `,v===1)We+=`
          let w_offset = ${Ve.indicesToOffset(`${Ve.type.indices}(u32(wRPerm), u32(wCPerm), inputChannel, wOutChannel)`)};
          let wValue = ${Ve.getByOffset(`w_offset / ${R}`)};
          dotProd = dotProd + xValue * wValue;`;else for(let Ie=0;Ie<v;Ie++)We+=`
            let wValue${Ie} = ${Ve.getByOffset(`${Ve.indicesToOffset(`${Ve.type.indices}(u32(wRPerm), u32(wCPerm), inputChannel + ${Ie}, wOutChannel)`)} / ${R}`)};
            dotProd = dotProd + xValue[${Ie}] * wValue${Ie};`;return We},Te=()=>{if(C===0)return"";if(!x)throw new Error(`packInputAs4 ${x} is not true.`);let We="";if(v===1){We+="dotProd = dotProd";for(let Ie=0;Ie<C;Ie++)We+=`
            + ${ze.getByOffset(`x_offset + ${Ie}`)} * ${Ve.getByOffset(`w_offset + ${Ie}`)}`;We+=";"}else if(v===2){if(C!==2)throw new Error(`Invalid inputChannelsRemainder ${C}.`);We+=`
          let xValue = ${ze.getByOffset("x_offset")};
          let wValue = ${Ve.getByOffset("w_offset")};
          dotProd = dotProd + dot(xValue, wValue);`}return We},Be=`
            let outputIndices = ${Ce.offsetToIndices(`global_idx * ${A}`)};
            let batch = ${Ce.indicesGet("outputIndices",0)};
            let d1 = ${Ce.indicesGet("outputIndices",qe)};
            let r = ${Ce.indicesGet("outputIndices",Me)};
            let c = ${Ce.indicesGet("outputIndices",pe)};
            let dyCorner = vec2<i32>(i32(r), i32(c)) - uniforms.pads;
            let dyRCorner = dyCorner.x;
            let dyCCorner = dyCorner.y;
            let groupId = d1 / uniforms.output_channels_per_group;
            let wOutChannel = d1 - groupId * uniforms.output_channels_per_group;
            // Convolve dy(?, ?, d2) with w(:, :, d1, d2) to compute dx(xR, xC, d1).
            // ? = to be determined. : = across all values in that axis.
            var dotProd = ${Ce.type.value}(0.0);
            var wR: u32 = 0;
            if (uniforms.dilations.x == 1) {
              // Minimum wR >= 0 that satisfies (dyRCorner + wR) % (uniforms.strides.x) == 0
              wR = u32(((dyRCorner + i32(uniforms.strides.x) - 1) / i32(uniforms.strides.x)) * i32(uniforms.strides.x) - dyRCorner);
            }
            for (; wR < uniforms.effective_filter_dims.x; wR = wR + 1) {
              if (wR % uniforms.dilations.x != 0) {
                continue;
              }
              let dyR = (${Ue}(dyRCorner) + ${Ue}(wR)) / ${Ue}(uniforms.strides[0]);
              let wRPerm = uniforms.filter_dims.x - 1 - wR / uniforms.dilations.x;
              if (dyR < 0.0 || dyR >= ${Ue}(uniforms.Dy_shape[${Me}]) || fract(dyR) > 0.0 ||
                  wRPerm < 0) {
                continue;
              }
              let idyR: u32 = u32(dyR);
              var wC: u32 = 0;
              if (uniforms.dilations.y == 1) {
                // Minimum wC >= 0 that satisfies (dyCCorner + wC) % (uniforms.strides.y) == 0
                wC = u32(((dyCCorner + i32(uniforms.strides.y) - 1) / i32(uniforms.strides.y)) * i32(uniforms.strides.y) - dyCCorner);
              }
              for (; wC < uniforms.effective_filter_dims.y; wC = wC + 1) {
                if (wC % uniforms.dilations.y != 0) {
                  continue;
                }
                let dyC = (${Ue}(dyCCorner) + ${Ue}(wC)) / ${Ue}(uniforms.strides.y);
                let wCPerm = uniforms.filter_dims.y - 1 - wC / uniforms.dilations.y;
                if (dyC < 0.0 || dyC >= ${Ue}(uniforms.Dy_shape[${pe}]) ||
                    fract(dyC) > 0.0 || wCPerm < 0) {
                  continue;
                }
                let idyC: u32 = u32(dyC);
                var inputChannel = groupId * uniforms.input_channels_per_group;
                ${x?`
                var x_offset = ${ze.indicesToOffset(`${ze.type.indices}(batch, idyR, idyC, inputChannel)`)} / ${v};
                var w_offset = ${Ve.indicesToOffset(`${Ve.type.indices}(wRPerm, wCPerm, inputChannel, wOutChannel)`)} / ${R};
                  `:""}
                for (var d2: u32 = 0; d2 < uniforms.input_channels_per_group_int; d2 = d2 + ${x?4:v}) {
                  ${nt()}
                  inputChannel = inputChannel + ${x?4:v};
                }
                ${Te()}
                wC = wC + uniforms.strides.y - 1;
              }
              wR = wR + uniforms.strides[0] - 1;
            }
            let value = dotProd${s?` + bias[d1 / ${A}]`:""};
            ${Ce.setByOffset("global_idx","value")};
          `;return`
    ${_e.registerUniforms(Re).declareVariables(...ht,Ce)}
      ${_e.mainStart()}
      ${_e.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")};
    ${Be}}`};return{name:"ConvTranspose2D",shaderCache:{hint:`${r.cacheKey};${v}${R}${A}${x}${C}`,inputDependencies:P},getRunData:()=>({dispatchGroup:{x:U[0],y:U[1],z:U[2]},outputs:[{dims:a?a(o):o,dataType:e[0].dataType}],programUniforms:B}),getShaderSource:me}}}),Py,Uy,Ly,Bd,r$,qy,Md,Vy,i$,h1=Ee(()=>{c1(),Hn(),nn(),Py=(e,r,a,s,o,p)=>(e-1)*r+a+(s-1)*o+1-p,Uy=(e,r,a,s,o)=>{let p=Math.floor(e/2);r==="SAME_UPPER"?(a[s]=p,a[o]=e-p):r==="SAME_LOWER"&&(a[s]=e-p,a[o]=p)},Ly=(e,r,a,s,o,p,d,g,m,_)=>{let v=e.length-2,x=_.length===0;m.length<v&&m.push(...Array(v-m.length).fill(0));let T=e[0],C=r[g?3:1]*o;for(let A=0,R=e.length-v-(g?1:0);A<v;++A,++R){let H=e[R],U=x?H*d[A]:_[A],P=Py(H,d[A],p[A],r[R],a[A],U);Uy(P,s,p,A,A+v),x&&_.push(d[A]*(H-1)+m[A]+(r[R]-1)*a[A]+1-p[A]-p[A+v])}_.splice(0,0,T),_.splice(g?3:1,0,C)},Bd=(e,r)=>{let a=e.kernelShape.slice();if(e.kernelShape.length===0||e.kernelShape.reduce((x,T)=>x*T,1)===0){a.length=0;for(let x=2;x<r[1].dims.length;++x)a.push(r[1].dims[x])}let s=e.format==="NHWC";a.splice(0,0,r[1].dims[0]),a.splice(s?3:1,0,r[1].dims[1]);let o=e.pads.slice(),p=e.outputShape.slice(),d=e.outputPadding.slice(),g=r[0].dims,m=e.dilations.slice();if(m.reduce((x,T)=>x+T,0)===0){let x=r[0].dims.length-2;m=new Array(x).fill(1)}let _=e.strides.slice();if(_.reduce((x,T)=>x+T,0)===0){let x=r[0].dims.length-2;_=new Array(x).fill(1)}Ly(g,a,m,e.autoPad,e.group,o,_,s,d,p);let v=Object.assign({},e);return Object.assign(v,{kernelShape:a,pads:o,outputPadding:d,outputShape:p,dilations:m,strides:_}),v},r$=e=>{let r=Pp(e),a=e.format,s=["NOTSET","VALID","SAME_UPPER","SAME_LOWER"][typeof e.autoPad>"u"?0:e.autoPad],o=e.dilations,p=e.group??1,d=e.kernelShape,g=e.pads,m=e.strides,_=e.wIsConst(),v=e.outputPadding,x=e.outputShape;return{autoPad:s,format:a,dilations:o,group:p,kernelShape:d,outputPadding:v,outputShape:x,pads:g,strides:m,wIsConst:_,...r,cacheKey:`${e.format};${r.activation};`}},qy=(e,r)=>{if(!e||e.length!==2&&e.length!==3)throw new Error("Conv requires 2 or 3 inputs");if(e[0].dims.length!==4&&e[0].dims.length!==3)throw new Error("currently only support 2-dimensional conv");if(e[0].dims.length!==e[1].dims.length)throw new Error("filter does not have same dimension as input");let a=e[0].dims[r.format==="NHWC"?e[0].dims.length-1:1],s=e[1].dims[0];if(a!==s)throw new Error("FILTER_IN_CHANNEL should be equal to DATA_CHANNEL");let o=e[1].dims[1]*r.group;if(e.length===3&&(e[2].dims.length!==1||e[2].dims[0]!==o))throw new Error("invalid bias");let p=e[0].dims.length-2;if(r.dilations.reduce((d,g)=>d+g,0)>0&&r.dilations.length!==p)throw new Error(`dilations should be ${p}D`);if(r.strides.reduce((d,g)=>d+g,0)>0&&r.strides.length!==p)throw new Error(`strides should be ${p}D`);if(r.pads.reduce((d,g)=>d+g,0)>0&&r.pads.length!==p*2)throw new Error(`pads should be ${p*2}D`);if(r.outputPadding.length!==p&&r.outputPadding.length!==0)throw new Error(`output_padding should be ${p}D`);if(r.kernelShape.reduce((d,g)=>d+g,0)>0&&r.kernelShape.length!==0&&r.kernelShape.length!==e[1].dims.length-2)throw new Error("invalid kernel shape");if(r.outputShape.length!==0&&r.outputShape.length!==e[0].dims.length-2)throw new Error("invalid output shape")},Md=(e,r,a,s)=>{let o=e.kernelCustomData.wT??e.compute(si(r[1],[2,3,0,1]),{inputs:[1],outputs:[a.wIsConst?-2:-1]})[0];a.wIsConst&&!e.kernelCustomData.wT&&(e.kernelCustomData.wT=o);let p=[r[0],o];r.length===3&&p.push(r[2]),e.compute(t$(p,a,s),{inputs:p})},Vy=(e,r)=>{let a=r.format==="NHWC",s=[e.inputs[0].reshape(a?[e.inputs[0].dims[0],1,e.inputs[0].dims[1],e.inputs[0].dims[2]]:[e.inputs[0].dims[0],e.inputs[0].dims[1],1,e.inputs[0].dims[2]]),e.inputs[1].reshape([e.inputs[1].dims[0],e.inputs[1].dims[1],1,e.inputs[1].dims[2]])];e.inputs.length===3&&s.push(e.inputs[2]);let o=r.kernelShape;(o.length===0||o[0]===0)&&(o=[e.inputs[1].dims[2]]);let p=r.dilations;(p.length===0||p[0]===0)&&(p=[1]);let d=r.strides;(d.length===0||d[0]===0)&&(d=[1]);let g=r.pads;g.length===0&&(g=[0,0]),g=[0,g[0],0,g[1]],d=[1].concat(d),p=[1].concat(p),o=[1].concat(o);let m=r.outputPadding;m=[0].concat(m);let _=Bd({...r,pads:g,strides:d,dilations:p,kernelShape:o,outputPadding:m},s);Md(e,s,_,v=>a?[v[0],v[2],v[3]]:[v[0],v[1],v[3]])},i$=(e,r)=>{if(qy(e.inputs,r),e.inputs[0].dims.length===3)Vy(e,r);else{let a=Bd(r,e.inputs);Md(e,e.inputs,a)}}}),Wy,a$,n$,f1=Ee(()=>{ut(),ct(),Jt(),ft(),Wy=(e,r,a,s)=>{let o=ge.size(r),p=r.length,d=$e("input",e,p),g=je("output",e,p),m=a.dataType===6?a.getInt32Array()[0]:Number(a.getBigInt64Array()[0]),_=ge.normalizeAxis(m,p),v=x=>{let T=` i32(${d.indicesGet("inputIndices","uniforms.axis")}) `,C=Qe("uniforms.input_shape","uniforms.axis",p),A=s.reverse?T+(s.exclusive?" + 1":""):"0",R=s.reverse?C:T+(s.exclusive?"":" + 1");return`
                ${x.registerUniform("outputSize","u32").registerUniform("axis","u32").declareVariables(d,g)}
                ${x.mainStart()}
                  ${x.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
                  var inputIndices = ${g.offsetToIndices("global_idx")};
                  var sum = ${g.type.value}(0);
                  let first : i32 = ${A};
                  let last : i32 = ${R};
                  for (var i : i32 = first; i < last; i++) {
                    ${d.indicesSet("inputIndices","uniforms.axis","u32(i)")};
                    sum = sum + ${d.getByIndices("inputIndices")};
                  }
                  ${g.setByOffset("global_idx","sum")};
                }`};return{name:"CumSum",shaderCache:{hint:s.cacheKey,inputDependencies:["rank"]},getRunData:()=>({outputs:[{dims:r,dataType:e}],dispatchGroup:{x:Math.ceil(o/64)},programUniforms:[{type:12,data:o},{type:12,data:_},...Je(r,r)]}),getShaderSource:v}},a$=(e,r)=>{let a=e.inputs[0].dims,s=e.inputs[0].dataType,o=e.inputs[1];e.compute(Wy(s,a,o,r),{inputs:[0]})},n$=e=>{let r=e.exclusive===1,a=e.reverse===1;return Nt({exclusive:r,reverse:a})}}),Gy,Fy,Hy,s$,o$,m1=Ee(()=>{ut(),ct(),Jt(),ft(),Gy=e=>{if(!e||e.length!==1)throw new Error("DepthToSpace requires 1 input.");if(e[0].dims.length!==4)throw new Error("DepthToSpace requires 4D input.")},Fy=(e,r,a,s)=>{let o=[];o.push(`fn perm(i: ${s.type.indices}) -> ${a.type.indices} {
    var a: ${a.type.indices};`);for(let p=0;p<r;++p)o.push(a.indicesSet("a",e[p],`i[${p}]`));return o.push("return a;}"),o.join(`
`)},Hy=(e,r)=>{let a,s,o,p,d,g,m=r.format==="NHWC",_=r.blocksize,v=r.mode==="DCR";m?([a,s,o,p]=e.dims,d=v?[a,s,o,_,_,p/_**2]:[a,s,o,p/_**2,_,_],g=v?[0,1,3,2,4,5]:[0,1,4,2,5,3]):([a,s,o,p]=[e.dims[0],e.dims[2],e.dims[3],e.dims[1]],d=v?[a,_,_,p/_**2,s,o]:[a,p/_**2,_,_,s,o],g=v?[0,3,4,1,5,2]:[0,1,4,2,5,3]);let x=e.reshape(d),T=x.dims.length,C=e.dataType,A=$e("a",C,T),R=je("output",C,T),H=U=>`
  ${U.registerUniform("output_size","u32").declareVariables(A,R)}

  ${Fy(g,T,A,R)}

  ${U.mainStart()}
    ${U.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let indices = ${R.offsetToIndices("global_idx")};
    let aIndices = perm(indices);

    ${R.setByOffset("global_idx",A.getByIndices("aIndices"))}
  }`;return{name:"DepthToSpace",shaderCache:{hint:`${e.dims};${r.blocksize};${r.mode}`,inputDependencies:["rank"]},getRunData:U=>{let P=m?[a,s*_,o*_,p/_**2]:[a,p/_**2,s*_,o*_],F=ge.size(P),G=x.dims,K=ge.sortBasedOnPerm(G,g);return{outputs:[{dims:P,dataType:U[0].dataType}],dispatchGroup:{x:Math.ceil(F/64)},programUniforms:[{type:12,data:F},...Je(G,K)]}},getShaderSource:H}},s$=(e,r)=>{Gy(e.inputs),e.compute(Hy(e.inputs[0],r))},o$=e=>Nt({blocksize:e.blocksize,mode:e.mode,format:e.format})}),Ku,Ro,Dd,jy,Ky,Zy,Qy,Nd,Xy,u$,l$,g1=Ee(()=>{ut(),ct(),Jt(),ft(),Ku="[a-zA-Z]|\\.\\.\\.",Ro="("+Ku+")+",Dd="^"+Ro+"$",jy="("+Ro+",)*"+Ro,Ky="^"+jy+"$",Zy=class{constructor(e=-1){this.symbolToIndices=new Map,this.inputIndex=e}addSymbol(e,r){let a=this.symbolToIndices.get(e);a===void 0?a=[r]:a.push(r),this.symbolToIndices.set(e,a)}},Qy=class{constructor(e,r){var o;this.equation=r,this.hasEllipsis=!1,this.symbolToInfo=new Map,this.lhs=new Array,this.outputDims=[];let[a,s]=r.includes("->")?r.split("->",2):[r,""];if(!a.match(RegExp(Ky)))throw new Error("Invalid LHS term");if(a.split(",").forEach((p,d)=>{let g=e[d].dims.slice();if(!p.match(RegExp(Dd)))throw new Error("Invalid LHS term");let m=this.processTerm(p,!0,g,d);this.lhs.push(m)}),s==="")s+=[...this.symbolToInfo.entries()].filter(([p,d])=>d.count===1||p==="...").map(([p])=>p).join("");else if(!s.match(RegExp(Ro)))throw new Error("Invalid RHS");(o=s.match(RegExp(Ku,"g")))==null||o.forEach(p=>{if(p==="...")this.outputDims=this.outputDims.concat(this.ellipsisDims);else{let d=this.symbolToInfo.get(p);if(d===void 0)throw new Error("Invalid RHS symbol");this.outputDims.push(d.dimValue)}}),this.rhs=this.processTerm(s,!1,this.outputDims)}addSymbol(e,r,a){let s=this.symbolToInfo.get(e);if(s!==void 0){if(s.dimValue!==r&&s.count!==1)throw new Error("Dimension mismatch");s.count++,s.inputIndices.push(a)}else s={count:1,dimValue:r,inputIndices:[a]};this.symbolToInfo.set(e,s)}processTerm(e,r,a,s=-1){let o=a.length,p=!1,d=[],g=0;if(!e.match(RegExp(Dd))&&!r&&e!=="")throw new Error("Invalid LHS term");let m=e.match(RegExp(Ku,"g")),_=new Zy(s);return m==null||m.forEach((v,x)=>{if(v==="..."){if(p)throw new Error("Only one ellipsis is allowed per input term");p=!0;let T=o-m.length+1;if(T<0)throw new Error("Ellipsis out of bounds");if(d=a.slice(g,g+T),this.hasEllipsis){if(this.ellipsisDims.length!==d.length||this.ellipsisDims.toString()!==d.toString())throw new Error("Ellipsis dimensions mismatch")}else if(r)this.hasEllipsis=!0,this.ellipsisDims=d;else throw new Error("Ellipsis must be specified in the LHS");for(let C=0;C<d.length;C++){let A=String.fromCharCode(48+C);_.addSymbol(A,x+C),this.addSymbol(A,a[g++],s)}}else _.addSymbol(v,x+(this.hasEllipsis?this.ellipsisDims.length-1:0)),this.addSymbol(v,a[g++],s)}),_}},Nd=e=>e+"_max",Xy=(e,r,a,s)=>{let o=e.map(_=>_.length).map((_,v)=>$e(`input${v}`,r,_)),p=ge.size(s),d=je("output",r,s.length),g=[...a.symbolToInfo.keys()].filter(_=>!a.rhs.symbolToIndices.has(_)),m=_=>{let v=[],x="var prod = 1.0;",T="var sum = 0.0;",C="sum += prod;",A=[],R=[],H=[],U=[],P=a.symbolToInfo.size===a.rhs.symbolToIndices.size;a.symbolToInfo.forEach((G,K)=>{var ee;if(a.rhs.symbolToIndices.has(K)){let ae=(ee=a.rhs.symbolToIndices.get(K))==null?void 0:ee[0];ae!==void 0&&a.lhs.forEach((B,me)=>{if(G.inputIndices.includes(me)){let _e=B.symbolToIndices.get(K);if(_e===void 0)throw new Error("Invalid symbol error");_e.forEach(Re=>{v.push(`${o[me].indicesSet(`input${me}Indices`,Re,d.indicesGet("outputIndices",ae))}`)})}})}else a.lhs.forEach((ae,B)=>{if(G.inputIndices.includes(B)){let me=ae.symbolToIndices.get(K);if(me===void 0)throw new Error("Invalid symbol error");me.forEach(_e=>{A.push(`${o[B].indicesSet(`input${B}Indices`,_e,`${K}`)}`)}),U.push(`prod *= ${o[B].getByIndices(`input${B}Indices`)};`)}}),R.push(`for(var ${K}: u32 = 0; ${K} < uniforms.${Nd(K)}; ${K}++) {`),H.push("}")});let F=P?[...v,`let sum = ${o.map((G,K)=>G.getByIndices(`input${K}Indices`)).join(" * ")};`]:[...v,T,...R,...A,x,...U,C,...H];return`
            ${_.registerUniforms(g.map(G=>({name:`${Nd(G)}`,type:"u32"}))).registerUniform("outputSize","u32").declareVariables(...o,d)}

            ${_.mainStart()}
            ${_.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
            var outputIndices = ${d.offsetToIndices("global_idx")};
            ${o.map((G,K)=>`var input${K}Indices: ${o[K].type.indices};`).join(`
`)}
            ${F.join(`
`)};
            ${d.setByOffset("global_idx","sum")};
          }`};return{name:"Einsum",shaderCache:{hint:a.equation,inputDependencies:e.map(()=>"rank")},getRunData:()=>{let _=g.filter(x=>a.symbolToInfo.has(x)).map(x=>{var T;return{type:12,data:((T=a.symbolToInfo.get(x))==null?void 0:T.dimValue)||0}});_.push({type:12,data:p});let v=e.map((x,T)=>[...Je(x)]).reduce((x,T)=>x.concat(T),_);return v.push(...Je(s)),{outputs:[{dims:s,dataType:r}],dispatchGroup:{x:Math.ceil(p/64)},programUniforms:v}},getShaderSource:m}},u$=(e,r)=>{let a=new Qy(e.inputs,r.equation),s=a.outputDims,o=e.inputs.map((p,d)=>p.dims);e.compute(Xy(o,e.inputs[0].dataType,a,s))},l$=e=>{let r=e.equation.replace(/\s+/g,"");return Nt({equation:r})}}),Yy,Pd,Jy,e_,d$,y1=Ee(()=>{ut(),ct(),ft(),Yy=e=>{if(!e||e.length!==2)throw new Error("Expand requires 2 input.");let r=e[0].dims,a=Array.from(e[1].getBigInt64Array(),Number),s=a.length<r.length?0:a.length-r.length,o=r.length<a.length?0:r.length-a.length;for(;s<a.length&&o<r.length;++s,++o)if(a[s]!==r[o]&&a[s]!==1&&r[o]!==1)throw new Error("Expand requires shape to be broadcastable to input")},Pd=(e,r)=>{let a=e.length-r.length,s=[];for(let o=0;o<a;++o)s.push(e[o]);for(let o=0;o<r.length;++o)s.push(r[o]===1?e[o+a]:r[o]);return s},Jy=(e,r)=>e.length>r.length?Pd(e,r):Pd(r,e),e_=e=>{let r=e[0].dims,a=Array.from(e[1].getBigInt64Array(),Number),s=Jy(r,a),o=e[0].dataType,p=o===9||ge.size(r)===1,d=o===9||r.length>0&&r[r.length-1]%4===0?4:1,g=p||s.length>0&&s[s.length-1]%4===0?4:1,m=Math.ceil(ge.size(s)/g),_=x=>{let T=$e("input",o,r.length,d),C=je("output",o,s.length,g),A;if(o===9){let R=(H,U,P="")=>`
          let outputIndices${U} = ${C.offsetToIndices(`outputOffset + ${U}u`)};
          let offset${U} = ${T.broadcastedIndicesToOffset(`outputIndices${U}`,C)};
          let index${U} = offset${U} / 4u;
          let component${U} = offset${U} % 4u;
          ${H}[${U}] = ${P}(${T.getByOffset(`index${U}`)}[component${U}]);
        `;A=`
        let outputOffset = global_idx * ${g};
        var data = vec4<u32>(0);
        ${R("data",0,"u32")}
        ${R("data",1,"u32")}
        ${R("data",2,"u32")}
        ${R("data",3,"u32")}
        ${C.setByOffset("global_idx","data")}
      }`}else A=`
        let outputIndices = ${C.offsetToIndices(`global_idx * ${g}`)};
        let inputOffset = ${T.broadcastedIndicesToOffset("outputIndices",C)};
        let data = ${C.type.value}(${T.getByOffset(`inputOffset / ${d}`)});
        ${C.setByOffset("global_idx","data")}
      }`;return`
    ${x.registerUniform("vec_size","u32").declareVariables(T,C)}
    ${x.mainStart()}
    ${x.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}
    ${A}`},v=[{type:12,data:m},...Je(r,s)];return{name:"Expand",shaderCache:{hint:`${s.length};${d}${g}`,inputDependencies:["rank"]},getShaderSource:_,getRunData:()=>({outputs:[{dims:s,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(m/64)},programUniforms:v})}},d$=e=>{Yy(e.inputs),e.compute(e_(e.inputs),{inputs:[0]})}}),t_,p$,_1=Ee(()=>{ut(),ct(),ft(),Np(),t_=e=>{let r=e[0].dataType,a=ge.size(e[0].dims),s=ge.size(e[1].dims),o=s%4===0,p=d=>{let g=$e("x",r,[1],4),m=$e("bias",r,[1],4),_=je("y",r,[1],4),v=[{name:"output_vec_size",type:"u32"},{name:"bias_size",type:"u32"}],x=C=>`
      let bias${C}_offset: u32 = (global_idx * 4 + ${C}) % uniforms.bias_size;
      let bias${C} = ${m.getByOffset(`bias${C}_offset / 4`)}[bias${C}_offset % 4];`,T=o?`
      let bias = ${m.getByOffset("global_idx % (uniforms.bias_size / 4)")};`:`${x(0)}${x(1)}${x(2)}${x(3)}
      let bias = ${g.type.value}(bias0, bias1, bias2, bias3);`;return`${d.registerUniforms(v).declareVariables(g,m,_)}

    ${pp(Or(r))}

    ${d.mainStart(gs)}
      ${d.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_vec_size")}

      let x = ${g.getByOffset("global_idx")};
      ${T}
      let x_in = x + bias;
      ${_.setByOffset("global_idx",cp("x_in"))}
    }`};return{name:"FastGeluWithBias",shaderCache:{hint:`${o}`,inputDependencies:["type","type"]},getShaderSource:p,getRunData:d=>({outputs:[{dims:d[0].dims,dataType:d[0].dataType}],programUniforms:[{type:12,data:Math.ceil(a/4)},{type:12,data:s}],dispatchGroup:{x:Math.ceil(a/gs/4)}})}},p$=e=>{e.inputs.length<2||ge.size(e.inputs[1].dims)===0?Ab(e):e.compute(t_(e.inputs))}}),r_,i_,c$,h$,w1=Ee(()=>{ut(),ct(),Jt(),ft(),r_=e=>{if(!e||e.length!==2)throw new Error("Gather requires 2 inputs.")},i_=(e,r)=>{let a=e[0].dims,s=e[1].dims,o=a.length,p=ge.normalizeAxis(r.axis,o),d=a.slice(0);d.splice(p,1,...s);let g=a[p],m=e[0].dataType===9?4:1,_=Math.ceil(ge.size(d)/m),v=[{type:12,data:_},{type:6,data:g},{type:12,data:p},...Je(e[0].dims,e[1].dims,d)],x=T=>{let C=$e("data",e[0].dataType,e[0].dims.length,m),A=$e("inputIndices",e[1].dataType,e[1].dims.length),R=je("output",e[0].dataType,d.length,m),H=P=>{let F=s.length,G=`var indicesIndices${P}  = ${A.type.indices}(0);`;for(let K=0;K<F;K++)G+=`${F>1?`indicesIndices${P}[${K}]`:`indicesIndices${P}`} = ${d.length>1?`outputIndices${P}[uniforms.axis + ${K}]`:`outputIndices${P}`};`;G+=`
          var idx${P} = ${A.getByIndices(`indicesIndices${P}`)};
          if (idx${P} < 0) {
            idx${P} = idx${P} + uniforms.axisDimLimit;
          }
          var dataIndices${P} : ${C.type.indices};
        `;for(let K=0,ee=0;K<o;K++)K===p?(G+=`${o>1?`dataIndices${P}[${K}]`:`dataIndices${P}`} = u32(idx${P});`,ee+=F):(G+=`${o>1?`dataIndices${P}[${K}]`:`dataIndices${P}`} = ${d.length>1?`outputIndices${P}[${ee}]`:`outputIndices${P}`};`,ee++);return G},U;if(e[0].dataType===9){let P=(F,G,K="")=>`
          let outputIndices${G} = ${R.offsetToIndices(`outputOffset + ${G}u`)};
          ${H(G)};
          let offset${G} = ${C.indicesToOffset(`dataIndices${G}`)};
          let index${G} = offset${G} / 4u;
          let component${G} = offset${G} % 4u;
          ${F}[${G}] = ${K}(${C.getByOffset(`index${G}`)}[component${G}]);
        `;U=`
        let outputOffset = global_idx * ${m};
        var value = vec4<u32>(0);
        ${P("value",0,"u32")}
        ${P("value",1,"u32")}
        ${P("value",2,"u32")}
        ${P("value",3,"u32")}
        ${R.setByOffset("global_idx","value")}
      `}else U=`
      let outputIndices = ${R.offsetToIndices("global_idx")};
      ${H("")};
      let value = ${C.getByIndices("dataIndices")};
      ${R.setByOffset("global_idx","value")};
      `;return`
      ${T.registerUniform("outputSize","u32").registerUniform("axisDimLimit","i32").registerUniform("axis","u32").declareVariables(C,A,R)}
      ${T.mainStart()}
        ${T.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
        ${U}
      }`};return{name:"Gather",shaderCache:{hint:r.cacheKey,inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:d,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(_/64)},programUniforms:v}),getShaderSource:x}},c$=e=>Nt({axis:e.axis}),h$=(e,r)=>{let a=e.inputs;r_(a),e.compute(i_(e.inputs,r))}}),a_,f$,m$,b1=Ee(()=>{ut(),ct(),ft(),a_=(e,r,a,s,o,p,d,g,m)=>{let _=[{type:12,data:p},{type:12,data:s},{type:12,data:o},{type:12,data:a},{type:12,data:d},{type:12,data:g},{type:12,data:m}],v=[p];_.push(...Je(r.dims,v));let x=T=>{let C=$e("indices_data",r.dataType,r.dims.length),A=je("input_slice_offsets_data",12,1,1),R=[C,A],H=[{name:"output_size",type:"u32"},{name:"batch_dims",type:"u32"},{name:"input_dims",type:"u32",length:o.length},{name:"sizes_from_slice_dims_data",type:"u32",length:a.length},{name:"num_slices_per_batch",type:"u32"},{name:"input_batch_stride",type:"u32"},{name:"num_slice_dims",type:"u32"}];return`
  ${T.registerUniforms(H).declareVariables(...R)}
  ${T.mainStart()}
    ${T.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let batch_idx = global_idx / uniforms.num_slices_per_batch;
    let base_offset = batch_idx * uniforms.input_batch_stride;

    let slice_indices_base_offset = global_idx * uniforms.num_slice_dims;
    var relative_slice_offset = 0;
    for (var dim_idx = 0u; dim_idx < uniforms.num_slice_dims; dim_idx ++) {
      var index = i32(indices_data[dim_idx + slice_indices_base_offset].x);
      let input_dim_idx = uniforms.batch_dims + dim_idx;
      if (index < 0) {
        ${o.length===1?"index += i32(uniforms.input_dims);":"index += i32(uniforms.input_dims[input_dim_idx]);"}
      }
      ${a.length===1?"relative_slice_offset += index * i32(uniforms.sizes_from_slice_dims_data);":"relative_slice_offset += index * i32(uniforms.sizes_from_slice_dims_data[dim_idx]);"}
    }

    input_slice_offsets_data[global_idx] =  base_offset + u32(relative_slice_offset);
  }`};return e.compute({name:"computeSliceOffsets",shaderCache:{hint:`${o.length}_${a.length}`,inputDependencies:["rank"]},getRunData:()=>({outputs:[{dims:v,dataType:e.inputs[1].dataType}],dispatchGroup:{x:Math.ceil(p/64)},programUniforms:_}),getShaderSource:x},{inputs:[r],outputs:[-1]})[0]},f$=(e,r)=>{let a=e.inputs,s=a[0].dims,o=a[0].dataType,p=a[1].dims,d=p[p.length-1],g=ge.sizeToDimension(p,p.length-1),m=ge.sizeFromDimension(s,r.batchDims+d),_=ge.sizeToDimension(s,r.batchDims),v=ge.sizeFromDimension(s,r.batchDims),x=g/_,T=new Array(d),C=m;for(let G=0;G<d;++G)T[d-1-G]=C,C*=s[r.batchDims+d-1-G];let A=a_(e,a[1],T,r.batchDims,s,g,x,v,d),R=r.batchDims+d;if(R>s.length)throw new Error("last dimension of indices must not be larger than rank of input tensor");let H=p.slice(0,-1).concat(s.slice(R)),U=ge.size(H),P=[{type:12,data:U},{type:12,data:m},...Je(a[0].dims,A.dims,H)],F=G=>{let K=$e("data",a[0].dataType,a[0].dims.length),ee=$e("slice_offsets",12,A.dims.length),ae=je("output",a[0].dataType,H.length);return`
          ${G.registerUniform("output_size","u32").registerUniform("slice_size","u32").declareVariables(K,ee,ae)}
            ${G.mainStart()}
            ${G.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
          let slice_offset = slice_offsets[global_idx / uniforms.slice_size];
          output[global_idx] = data[u32(slice_offset) + global_idx % uniforms.slice_size];
        }`};e.compute({name:"GatherND",shaderCache:{hint:r.cacheKey,inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:H,dataType:o}],dispatchGroup:{x:Math.ceil(U/64)},programUniforms:P}),getShaderSource:F},{inputs:[a[0],A]})},m$=e=>({batchDims:e.batch_dims,cacheKey:""})}),n_,s_,g$,y$,$1=Ee(()=>{ut(),ct(),Jt(),ft(),n_=(e,r)=>{if(e.length<3||e.length>4)throw new Error("GatherBlockQuantized requires 3 or 4 inputs.");let a=ge.normalizeAxis(r.quantizeAxis,e[0].dims.length),s=r.blockSize,o=e[0],p=e[2],d=e.length===4?e[3]:void 0;if(p.dims.length!==o.dims.length||!o.dims.map((g,m)=>m===a?Math.ceil(g/s)===p.dims[m]:g===p.dims[m]).reduce((g,m)=>g&&m,!0))throw new Error("Scales must have the same rank as the input tensor and the dims should match except on gatherAxis.");if(d){if(d.dataType!==o.dataType)throw new Error("Zero point must have the same data type as the input tensor.");if(d.dims.length!==p.dims.length||!d.dims.map((g,m)=>g===p.dims[m]).reduce((g,m)=>g&&m,!0))throw new Error("Zero point must have the same rank as the input tensor and the dims should match except on quantizeAxis.")}},s_=(e,r)=>{let a=e[0].dims,s=e[1].dims,o=a.length,p=ge.normalizeAxis(r.gatherAxis,o),d=ge.normalizeAxis(r.quantizeAxis,o),g=a.slice(0);g.splice(p,1,...s);let m=ge.size(g),_=e[2].dataType,v=e[0].dataType===22,x=[{type:12,data:m},{type:12,data:d},{type:12,data:p},{type:12,data:r.blockSize},...Je(...e.map((C,A)=>C.dims),g)],T=C=>{let A=$e("data",e[0].dataType,e[0].dims.length),R=$e("inputIndices",e[1].dataType,e[1].dims.length),H=$e("scales",e[2].dataType,e[2].dims.length),U=e.length>3?$e("zeroPoint",e[3].dataType,e[3].dims.length):void 0,P=je("output",_,g.length),F=[A,R,H];U&&F.push(U);let G=[{name:"output_size",type:"u32"},{name:"quantize_axis",type:"u32"},{name:"gather_axis",type:"u32"},{name:"block_size",type:"u32"}];return`
        ${C.registerUniforms(G).declareVariables(...F,P)}
        ${C.mainStart()}
        let output_indices = ${P.offsetToIndices("global_idx")};
        var indices_indices = ${R.type.indices}(0);
        ${s.length>1?`
          for (var i: u32 = 0; i < ${s.length}; i++) {
            let index = ${P.indicesGet("output_indices","uniforms.gather_axis + i")};
            ${R.indicesSet("indices_indices","i","index")};
          }`:`indices_indices = ${P.indicesGet("output_indices","uniforms.gather_axis")};`};
        var data_indices = ${A.type.indices}(0);
        for (var i: u32 = 0; i < uniforms.gather_axis; i++) {
          let index = ${P.indicesGet("output_indices","i")};
          ${A.indicesSet("data_indices","i","index")};
        }
        var index_from_indices = ${R.getByIndices("indices_indices")};
        if (index_from_indices < 0) {
          index_from_indices += ${a[p]};
        }
        ${A.indicesSet("data_indices","uniforms.gather_axis","u32(index_from_indices)")};
        for (var i = uniforms.gather_axis + 1; i < ${g.length}; i++) {
          let index = ${P.indicesGet("output_indices",`i + ${s.length} - 1`)};
          ${A.indicesSet("data_indices","i","index")};
        }
        let data_offset = ${A.indicesToOffset("data_indices")};
        let data_index = data_offset % 8;
        // Convert 4-bit packed data to 8-bit packed data.
        let packed_4bit_quantized_data = ${A.getByOffset("data_offset / 8")};
        let packed_8bit_quantized_data = (packed_4bit_quantized_data >> (4 * (data_index % 2))) & 0x0f0f0f0f;
        let quantized_data_vec = ${v?"unpack4xI8":"unpack4xU8"}(u32(packed_8bit_quantized_data));
        let quantized_data = quantized_data_vec[data_index / 2];
        var scale_indices = data_indices;
        let quantize_axis_index = ${H.indicesGet("data_indices","uniforms.quantize_axis")} / uniforms.block_size;
        ${H.indicesSet("scale_indices","uniforms.quantize_axis","quantize_axis_index")};
        var scale = ${H.getByIndices("scale_indices")};
        ${U?`
              let zero_point_indices = scale_indices;
              let zero_point_offset = ${U.indicesToOffset("zero_point_indices")};
              let zero_point_index = zero_point_offset % 8;
              let packed_4bit_zero_points = ${U.getByOffset("zero_point_offset / 8")};
              let packed_8bit_zero_points = (packed_4bit_zero_points >> (4 * (zero_point_index % 2))) & 0x0f0f0f0f;
              let zero_point_vec = ${v?"unpack4xI8":"unpack4xU8"}(u32(packed_8bit_zero_points));
              let zero_point = zero_point_vec[zero_point_index / 2];`:"var zero_point = 0"};
        let dequantized_data = ${Or(_)}(quantized_data - zero_point) * scale;
        ${P.setByOffset("global_idx","dequantized_data")};
    }`};return{name:"GatherBlockQuantized",shaderCache:{hint:`${r.cacheKey};${e.filter((C,A)=>A!==1).map(C=>C.dims.join("_")).join(";")}`,inputDependencies:Array.from({length:e.length},(C,A)=>"rank")},getRunData:()=>({outputs:[{dims:g,dataType:_}],dispatchGroup:{x:Math.ceil(m/64)},programUniforms:x}),getShaderSource:T}},g$=(e,r)=>{let a=e.inputs;n_(a,r),e.compute(s_(e.inputs,r))},y$=e=>Nt({blockSize:e.blockSize,gatherAxis:e.gatherAxis,quantizeAxis:e.quantizeAxis})}),o_,u_,_$,w$,v1=Ee(()=>{ut(),ct(),Jt(),ft(),o_=e=>{if(!e||e.length!==2)throw new Error("GatherElements requires 2 inputs.");if(e[0].dims.length<1)throw new Error("GatherElements requires that the data input be rank >= 1.");if(e[0].dims.length!==e[1].dims.length)throw new Error(`GatherElements requires that the data input and
                     indices input tensors be of same rank.`)},u_=(e,r)=>{let a=e[0].dims,s=e[0].dataType,o=a.length,p=e[1].dims,d=e[1].dataType,g=ge.normalizeAxis(r.axis,o),m=a[g],_=p.slice(0),v=ge.size(_),x=$e("input",s,o),T=$e("indicesInput",d,p.length),C=je("output",s,_.length),A=[{type:12,data:v},{type:6,data:m},{type:12,data:g}];return A.push(...Je(a,p,_)),{name:"GatherElements",shaderCache:{inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:_,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(v/64)},programUniforms:A}),getShaderSource:R=>`
      ${R.registerUniform("outputSize","u32").registerUniform("axisDimLimit","i32").registerUniform("axis","u32").declareVariables(x,T,C)}
      ${R.mainStart()}
      ${R.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}

      let outputIndices = ${C.offsetToIndices("global_idx")};

      var idx = ${T.getByOffset("global_idx")};
      if (idx < 0) {
        idx = idx + uniforms.axisDimLimit;
      }
      var inputIndices = ${x.type.indices}(outputIndices);
      ${x.indicesSet("inputIndices","uniforms.axis","u32(idx)")};
      let value = ${x.getByIndices("inputIndices")};

      ${C.setByOffset("global_idx","value")};
  }`}},_$=e=>Nt({axis:e.axis}),w$=(e,r)=>{let a=e.inputs;o_(a),e.compute(u_(e.inputs,r))}}),l_,d_,b$,$$,x1=Ee(()=>{ut(),ct(),ft(),l_=e=>{if(!e)throw new Error("Input is missing");if(e.length<2||e.length>3)throw new Error("Invaid input number.");if(e.length===3&&e[2].dims.length>2)throw new Error("Invalid input shape of C");if(e[0].dataType!==e[1].dataType||e.length===3&&e[0].dataType!==e[2].dataType)throw new Error("Input types are mismatched")},d_=(e,r)=>{let a=e[0].dims.slice(),s=e[1].dims.slice(),[o,p,d]=_0.getShapeOfGemmResult(a,r.transA,s,r.transB,e.length===3?e[2].dims:void 0),g=[o,p];if(!g)throw new Error("Can't use gemm on the given tensors");let m=16,_=Math.ceil(p/m),v=Math.ceil(o/m),x=!0,T=ge.size(g),C=[{type:12,data:x?_:T},{type:12,data:o},{type:12,data:p},{type:12,data:d},{type:1,data:r.alpha},{type:1,data:r.beta}],A=["type","type"];e.length===3&&(C.push(...Je(e[2].dims)),A.push("rank")),C.push(...Je(g));let R=U=>{let P="";r.transA&&r.transB?P="value += a[k * uniforms.M + m] * b[n * uniforms.K + k];":r.transA&&!r.transB?P="value += a[k * uniforms.M + m] * b[k * uniforms.N + n];":!r.transA&&r.transB?P="value += a[m * uniforms.K + k] * b[n * uniforms.K + k];":!r.transA&&!r.transB&&(P="value += a[m * uniforms.K + k] * b[k * uniforms.N + n];");let F=r.alpha===1?"":"value *= uniforms.alpha;",G=$e("a",e[0].dataType,e[0].dims),K=$e("b",e[1].dataType,e[1].dims),ee=G.type.value,ae=null,B=[G,K];e.length===3&&(ae=$e("c",e[2].dataType,e[2].dims.length),B.push(ae));let me=je("output",e[0].dataType,g.length);B.push(me);let _e=[{name:"output_size",type:"u32"},{name:"M",type:"u32"},{name:"N",type:"u32"},{name:"K",type:"u32"},{name:"alpha",type:"f32"},{name:"beta",type:"f32"}];return`
  ${U.registerUniforms(_e).declareVariables(...B)}

  ${U.mainStart()}
    ${U.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

    let m = global_idx / uniforms.N;
    let n = global_idx % uniforms.N;

    var value = ${ee}(0);
    for (var k: u32 = 0u; k < uniforms.K; k++) {
      ${P}
    }

    ${F}
    ${ae!=null?`let cOffset = ${ae.broadcastedIndicesToOffset("vec2(m, n)",me)}; value += ${ee}(uniforms.beta) * ${ae.getByOffset("cOffset")};`:""}
    output[global_idx] = value;
  }`},H=U=>{let P=$e("a",e[0].dataType,e[0].dims),F=$e("b",e[1].dataType,e[1].dims),G=null,K=[P,F];e.length===3&&(G=$e("c",e[2].dataType,e[2].dims.length),K.push(G));let ee=je("output",e[0].dataType,g.length);K.push(ee);let ae=[{name:"num_tile_n",type:"u32"},{name:"M",type:"u32"},{name:"N",type:"u32"},{name:"K",type:"u32"},{name:"alpha",type:"f32"},{name:"beta",type:"f32"}],B="",me="";r.transA&&r.transB?(me=`
      var col = tile_row_start + local_id.x;
      var row = k_start + local_id.y;
      if (col < uniforms.M && row < uniforms.K) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.M + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${P.type.value}(0);
      }

      col = k_start + local_id.x;
      row = tile_col_start + local_id.y;
      if (col < uniforms.K && row < uniforms.N) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.K + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${F.type.value}(0);
      }
      `,B="value += tile_a[k][local_id.y] * tile_b[local_id.x][k];"):r.transA&&!r.transB?(me=`
      var col = tile_row_start + local_id.x;
      var row = k_start + local_id.y;
      if (col < uniforms.M && row < uniforms.K) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.M + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${P.type.value}(0);
      }

      col = tile_col_start + local_id.x;
      row = k_start + local_id.y;
      if (col < uniforms.N && row < uniforms.K) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.N + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${F.type.value}(0);
      }
      `,B="value += tile_a[k][local_id.y] * tile_b[k][local_id.x];"):!r.transA&&r.transB?(me=`
      var col = k_start + local_id.x;
      var row = tile_row_start + local_id.y;
      if (col < uniforms.K && row < uniforms.M) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.K + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${P.type.value}(0);
      }

      col = k_start + local_id.x;
      row = tile_col_start + local_id.y;
      if (col < uniforms.K && row < uniforms.N) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.K + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${F.type.value}(0);
      }
      `,B="value += tile_a[local_id.y][k] * tile_b[local_id.x][k];"):!r.transA&&!r.transB&&(me=`
      var col = k_start + local_id.x;
      var row = tile_row_start + local_id.y;
      if (col < uniforms.K && row < uniforms.M) {
        tile_a[local_id.y][local_id.x] = a[row * uniforms.K + col];
      } else {
        tile_a[local_id.y][local_id.x] = ${P.type.value}(0);
      }

      col = tile_col_start + local_id.x;
      row = k_start + local_id.y;
      if (col < uniforms.N && row < uniforms.K) {
        tile_b[local_id.y][local_id.x] = b[row * uniforms.N + col];
      } else {
        tile_b[local_id.y][local_id.x] = ${F.type.value}(0);
      }
      `,B="value += tile_a[local_id.y][k] * tile_b[k][local_id.x];");let _e=r.alpha===1?"":"value *= uniforms.alpha;";return`
  ${U.registerUniforms(ae).declareVariables(...K)}
  var<workgroup> tile_a: array<array<${P.type.storage}, ${m}>, ${m}>;
  var<workgroup> tile_b: array<array<${F.type.storage}, ${m}>, ${m}>;
  ${U.mainStart([m,m,1])}
    let tile_col_start = (workgroup_index % uniforms.num_tile_n) * ${m};
    let tile_row_start = (workgroup_index / uniforms.num_tile_n) * ${m};
    let num_tiles = (uniforms.K - 1) / ${m} + 1;
    var k_start = 0u;
    var value = ${ee.type.value}(0);
    for (var t: u32 = 0u; t < num_tiles; t++) {
      ${me}
      k_start = k_start + ${m};
      workgroupBarrier();

      for (var k: u32 = 0u; k < ${m}; k++) {
        ${B}
      }
      workgroupBarrier();
    }

    ${_e}
    let m = tile_row_start + local_id.y;
    let n = tile_col_start + local_id.x;
    ${G!=null?`let cOffset = ${G.broadcastedIndicesToOffset("vec2(m, n)",ee)}; value += ${ee.type.value}(uniforms.beta) * ${G.getByOffset("cOffset")};`:""}
    if (m < uniforms.M && n < uniforms.N) {
      output[m * uniforms.N + n] = value;
    }
  }`};return x?{name:"GemmShared",shaderCache:{hint:`${r.cacheKey}`,inputDependencies:A},getRunData:()=>({outputs:[{dims:g,dataType:e[0].dataType}],dispatchGroup:{x:_*v},programUniforms:C}),getShaderSource:H}:{name:"Gemm",shaderCache:{hint:`${r.cacheKey}`,inputDependencies:A},getRunData:()=>({outputs:[{dims:g,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(T/64)},programUniforms:C}),getShaderSource:R}},b$=e=>{let r=e.transA,a=e.transB,s=e.alpha,o=e.beta;return{transA:r,transB:a,alpha:s,beta:o,cacheKey:`${e.transA};${e.transB};${e.alpha===1}`}},$$=(e,r)=>{l_(e.inputs),e.compute(d_(e.inputs,r))}}),Zi,xa,Rn,Bn,p_,c_,h_,f_,m_,g_,y_,__,v$,x$,S1=Ee(()=>{ut(),ct(),Jt(),ft(),[Zi,xa,Rn,Bn]=[0,1,2,3],p_=e=>{if(e[0].dims.length!==4)throw new Error("only 4-D tensor is supported.");if(e[0].dims.length!==e[1].dims.length)throw new Error("input dimensions must be equal to grid dimensions");if(e[0].dims.length-2!==e[1].dims[e[1].dims.length-1])throw new Error(`last dimension of grid must be equal to ${e[0].dims.length-2}`);if(e[0].dims[0]!==e[1].dims[0])throw new Error("grid batch size must match input batch size")},c_=`
  fn gs_get_cubic_coeffs(x: f32) -> vec4<f32> {
    let cubic_alpha = -0.75f;
    let x_abs = abs(x);
    var coeffs: vec4<f32>;
    coeffs[0] = (((cubic_alpha * (x_abs + 1) - 5 * cubic_alpha) * (x_abs + 1) + 8 * cubic_alpha) * (x_abs + 1) - 4 * cubic_alpha);
    coeffs[1] = (((cubic_alpha + 2) * x_abs - (cubic_alpha + 3)) * x_abs * x_abs + 1);
    coeffs[2] = (((cubic_alpha + 2) * (1 - x_abs) - (cubic_alpha + 3)) * (1 - x_abs) * (1 - x_abs) + 1);
    coeffs[3] = (((cubic_alpha * (2 - x_abs) - 5 * cubic_alpha) * (2 - x_abs) + 8 * cubic_alpha) * (2 - x_abs) - 4 * cubic_alpha);
    return coeffs;
  }
`,h_=e=>`
  fn gs_bicubic_interpolate(p: mat4x4<${e}>, x: f32, y: f32) -> ${e} {
    var v: vec4<f32>;
    var coeffs = gs_get_cubic_coeffs(x);
    for (var i = 0; i < 4; i++) {
      v[i] = coeffs[0] * p[i][0] + coeffs[1] * p[i][1] + coeffs[2] * p[i][2] + coeffs[3] * p[i][3];
    }
    coeffs = gs_get_cubic_coeffs(y);
    let pixel = ${e}(coeffs[0] * v[0] + coeffs[1] * v[1] + coeffs[2] * v[2] + coeffs[3] * v[3]);
    return pixel;
  }
`,f_=e=>`
  fn gs_denormalize(n: f32, length: i32) -> f32 {
    ${e.alignCorners===0?`
    // alignCorners: false => [-1, 1] to [-0.5, length - 0.5]
    return ((n + 1.0) * f32(length) - 1.0) / 2.0;
    `:`
    // alignCorners: true => [-1, 1] to [0, length - 1]
    return (n + 1.0) / 2.0 * (f32(length - 1));
    `}
  }
`,m_=e=>`
  ${e.paddingMode==="reflection"?`
      fn gs_reflect(x: i32, x_min: f32, x_max: f32) -> u32 {
        var dx = 0.0;
        var fx = f32(x);
        let range = x_max - x_min;
        if (fx < x_min) {
          dx = x_min - fx;
          let n = u32(dx / range);
          let r = dx - f32(n) * range;
          if (n % 2 == 0) {
            fx = x_min + r;
          } else {
            fx = x_max - r;
          }
        } else if (fx > x_max) {
          dx = fx - x_max;
          let n = u32(dx / range);
          let r = dx - f32(n) * range;
          if (n % 2 == 0) {
            fx = x_max - r;
          } else {
            fx = x_min + r;
          }
        }
        return u32(fx);
      }`:""}
`,g_=(e,r,a)=>`
  fn pixel_at_grid(r: i32, c: i32, H: i32, W: i32, batch: u32, channel: u32, border: vec4<f32>) -> ${r} {
     var pixel = ${r}(0);
     var indices = vec4<u32>(0);
     indices[${Zi}] = batch;
     indices[${xa}] = channel;`+(()=>{switch(a.paddingMode){case"zeros":return`
          if (r >= 0 && r < H && c >=0 && c < W) {
            indices[${Rn}] = u32(r);
            indices[${Bn}] = u32(c);
          } else {
            return ${r}(0);
          }
        `;case"border":return`
          indices[${Rn}] = u32(clamp(r, 0, H - 1));
          indices[${Bn}] = u32(clamp(c, 0, W - 1));
        `;case"reflection":return`
          indices[${Rn}] = gs_reflect(r, border[1], border[3]);
          indices[${Bn}] = gs_reflect(c, border[0], border[2]);
        `;default:throw new Error(`padding mode ${a.paddingMode} is not supported`)}})()+`
    return ${e.getByIndices("indices")};
  }
`,y_=(e,r,a)=>(()=>{switch(a.mode){case"nearest":return`
          let result = pixel_at_grid(i32(round(y)), i32(round(x)), H_in, W_in, indices[${Zi}], indices[${xa}], border);
        `;case"bilinear":return`
          let x1 = i32(floor(x));
          let y1 = i32(floor(y));
          let x2 = x1 + 1;
          let y2 = y1 + 1;

          let p11 = pixel_at_grid(y1, x1, H_in, W_in, indices[${Zi}], indices[${xa}], border);
          let p12 = pixel_at_grid(y1, x2, H_in, W_in, indices[${Zi}], indices[${xa}], border);
          let p21 = pixel_at_grid(y2, x1, H_in, W_in, indices[${Zi}], indices[${xa}], border);
          let p22 = pixel_at_grid(y2, x2, H_in, W_in, indices[${Zi}], indices[${xa}], border);

          let dx2 = ${r}(f32(x2) - x);
          let dx1 = ${r}(x - f32(x1));
          let dy2 = ${r}(f32(y2) - y);
          let dy1 = ${r}(y - f32(y1));
          let result = dy2 * (dx2 * p11 + dx1 * p12) + dy1 * (dx2 * p21 + dx1 * p22);
        `;case"bicubic":return`
          let x0 = i32(floor(x)) - 1;
          let y0 = i32(floor(y)) - 1;
          var p: mat4x4<${r}>;
          for (var h = 0; h < 4; h++) {
            for (var w = 0; w < 4; w++) {
              p[h][w] = pixel_at_grid(h + y0, w + x0, H_in, W_in, indices[${Zi}], indices[${xa}], border);
            }
          }

          let dx = x - f32(x0 + 1);
          let dy = y - f32(y0 + 1);
          let result = gs_bicubic_interpolate(p, dx, dy);
        `;default:throw new Error(`mode ${a.mode} is not supported`)}})()+`${e.setByOffset("global_idx","result")}`,__=(e,r)=>{let a=$e("x",e[0].dataType,e[0].dims.length),s=[e[1].dims[0],e[1].dims[1],e[1].dims[2]],o=$e("grid",e[1].dataType,s.length,2),p=[e[0].dims[0],e[0].dims[1],e[1].dims[1],e[1].dims[2]];r.format==="NHWC"&&(p=[e[0].dims[0],e[1].dims[1],e[1].dims[2],e[0].dims[3]],[Zi,xa,Rn,Bn]=[0,3,1,2]);let d=je("output",e[0].dataType,p.length),g=a.type.value,m=ge.size(p),_=[{type:12,data:m},...Je(e[0].dims,s,p)],v=x=>`
  ${x.registerUniform("output_size","u32").declareVariables(a,o,d)}
  ${c_}
  ${h_(g)}
  ${f_(r)}
  ${m_(r)}
  ${g_(a,g,r)}

  ${x.mainStart()}
    ${x.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
      let H_in = i32(uniforms.x_shape[${Rn}]);
      let W_in = i32(uniforms.x_shape[${Bn}]);

      ${r.alignCorners===0?`
      let x_min = -0.5;
      let x_max = f32(W_in) - 0.5;
      let y_min = -0.5;
      let y_max = f32(H_in) - 0.5;
      `:`
      let x_min = 0.0;
      let x_max = f32(W_in) - 1.0;
      let y_min = 0.0;
      let y_max = f32(H_in) - 1.0;
      `};
      let border = vec4<f32>(x_min, y_min, x_max, y_max);

      let indices = ${d.offsetToIndices("global_idx")};
      var grid_indices = vec3<u32>(indices[${Zi}], indices[${Rn}], indices[${Bn}]);
      let nxy = ${o.getByIndices("grid_indices")};
      var x = gs_denormalize(f32(nxy[0]), W_in);
      var y = gs_denormalize(f32(nxy[1]), H_in);

      ${y_(d,g,r)}
  }`;return{name:"GridSample",shaderCache:{hint:`${r.cacheKey}`,inputDependencies:["type","type"]},getRunData:x=>{let T=ge.size(p);return{outputs:[{dims:p,dataType:x[0].dataType}],dispatchGroup:{x:Math.ceil(T/64)},programUniforms:_}},getShaderSource:v}},v$=(e,r)=>{p_(e.inputs),e.compute(__(e.inputs,r))},x$=e=>Nt({alignCorners:e.align_corners,mode:e.mode,paddingMode:e.padding_mode,format:e.format})}),qr,w_,S$,Ud,b_,Vo,T$,k$=Ee(()=>{ut(),ct(),Jt(),Rp(),Dp(),ft(),nn(),qr=(e,r)=>e.length>r&&e[r].dims.length>0?e[r]:void 0,w_=(e,r)=>{let a=e[0],s=qr(e,1),o=qr(e,2),p=qr(e,3),d=qr(e,4),g=qr(e,5),m=qr(e,6),_=qr(e,7);if(a.dims.length!==3&&a.dims.length!==5)throw new Error("Input query is expected to have 3 or 5 dimensions");let v=a.dims[0],x=a.dims[1],T=a.dims.length===3?a.dims[2]:r.numHeads*a.dims[4],C=x,A=0,R=0,H=Math.floor(T/r.numHeads);if(m&&_&&ge.size(m.dims)&&ge.size(_.dims)){if(m.dims.length!==4)throw new Error('Input "past_key" is expected to have 4 dimensions');if(m.dims[0]!==v||m.dims[1]!==r.numHeads||m.dims[3]!==H)throw new Error('Input "past_key" shape (batch_size, num_heads, past_sequence_length, head_size)');if(_.dims[0]!==v||_.dims[1]!==r.numHeads||_.dims[3]!==H)throw new Error('Input "past_value" shape (batch_size, num_heads, past_sequence_length, head_size)');if(m.dims[2]!==_.dims[2])throw new Error('Input "past_key" and "past_value" shall have same dim 2 (past_sequence_length)');if(_.dims.length!==4)throw new Error('Input "past_value" is expected to have 4 dimensions');A=m.dims[2],R=m.dims[2]}else if(m&&ge.size(m.dims)||_&&ge.size(_.dims))throw new Error('Input "past_key" and "past_value" shall be both present or both absent');let U;if(s&&ge.size(s.dims)>0){if(a.dims.length!==3)throw new Error('Input "query" is expected to have 3 dimensions when key is given');if(s.dims.length<3||s.dims.length>5)throw new Error('Input "key" is expected to have 3, 4, or 5 dimensions');if(a.dims[0]!==s.dims[0])throw new Error('Input "query" and "key" shall have same dim 0 (batch size)');if(s.dims.length===3){if(s.dims[2]!==a.dims[2])throw new Error('Input "query" and "key" shall have same dim 2 (hidden_size)');U=2,C=s.dims[1]}else if(s.dims.length===5){if(s.dims[2]!==r.numHeads||s.dims[3]!==2||s.dims[4]!==H)throw new Error('Expect "key" shape (batch_size, kv_sequence_length, num_heads, 2, head_size) for packed kv');if(o)throw new Error('Expect "value" be none when "key" has packed kv format.');U=5,C=s.dims[1]}else{if(s.dims[1]!==r.numHeads||s.dims[3]!==H)throw new Error('Expect "key" shape (batch_size, num_heads, kv_sequence_length, head_size) for past_key');U=0,C=s.dims[2]}}else{if(a.dims.length!==5)throw new Error('Input "query" is expected to have 5 dimensions when key is empty');if(a.dims[2]!==r.numHeads||a.dims[3]!==3)throw new Error('Expect "query" shape (batch_size, kv_sequence_length, num_heads, 3, head_size) for packed kv');U=3}if(p&&ge.size(p.dims)>0){if(p.dims.length!==1)throw new Error('Input "bias" is expected to have 1 dimension');if(s&&s.dims.length===5&&s.dims[3]===2)throw new Error("bias is not allowed for packed kv.")}let P=A+C,F=0;if(d&&ge.size(d.dims)>0){F=8;let ae=d.dims;throw ae.length===1?ae[0]===v?F=1:ae[0]===3*v+2&&(F=3):ae.length===2&&ae[0]===v&&ae[1]===P&&(F=5),F===8?new Error('Input "key_padding_mask" shape shall be (batch_size) or (batch_size, total_sequence_length)'):new Error("Mask not supported")}let G=!1,K=T;if(o&&ge.size(o.dims)>0){if(o.dims.length!==3&&o.dims.length!==4)throw new Error('Input "value" is expected to have 3 or 4 dimensions');if(a.dims[0]!==o.dims[0])throw new Error('Input "query" and "value" shall have same dim 0 (batch_size)');if(o.dims.length===3){if(C!==o.dims[1])throw new Error('Input "key" and "value" shall have the same dim 1 (kv_sequence_length)');K=o.dims[2]}else{if(C!==o.dims[2])throw new Error('Input "key" and "value" shall have the same dim 2 (kv_sequence_length)');K=o.dims[1]*o.dims[3],G=!0}}let ee=!1;if(d&&ge.size(d.dims)>0)throw new Error("Key padding mask is not supported");if(g&&ge.size(g.dims)>0){if(g.dims.length!==4)throw new Error('Input "attention_bias" is expected to have 4 dimensions');if(g.dims[0]!==v||g.dims[1]!==r.numHeads||g.dims[2]!==x||g.dims[3]!==P)throw new Error('Expect "attention_bias" shape (batch_size, num_heads, sequence_length, total_sequence_length)')}return{batchSize:v,sequenceLength:x,pastSequenceLength:A,kvSequenceLength:C,totalSequenceLength:P,maxSequenceLength:R,inputHiddenSize:0,hiddenSize:T,vHiddenSize:K,headSize:H,vHeadSize:Math.floor(K/r.numHeads),numHeads:r.numHeads,isUnidirectional:!1,pastPresentShareBuffer:!1,maskFilterValue:r.maskFilterValue,maskType:F,scale:r.scale,broadcastResPosBias:ee,passPastInKv:G,qkvFormat:U}},S$=e=>Nt({...e}),Ud=Nt({perm:[0,2,1,3]}),b_=(e,r,a,s,o,p,d)=>{let g=[s,o,p],m=ge.size(g),_=[{type:12,data:m},{type:12,data:d},{type:12,data:p}],v=x=>{let T=je("qkv_with_bias",r.dataType,g),C=$e("qkv",r.dataType,g),A=$e("bias",a.dataType,g),R=[{name:"output_size",type:"u32"},{name:"bias_offset",type:"u32"},{name:"hidden_size",type:"u32"}];return`
  ${x.registerUniforms(R).declareVariables(C,A,T)}
  ${x.mainStart()}
    ${x.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let bias_offset_idx = (global_idx % uniforms.hidden_size) + uniforms.bias_offset;

    qkv_with_bias[global_idx] = qkv[global_idx] + bias[bias_offset_idx];
  }`};return e.compute({name:"MultiHeadAttentionAddBias",shaderCache:{inputDependencies:["type","type"]},getRunData:()=>({outputs:[{dims:g,dataType:r.dataType,gpuDataType:0}],dispatchGroup:{x:Math.ceil(m/64)},programUniforms:_}),getShaderSource:v},{inputs:[r,a],outputs:[-1]})[0]},Vo=(e,r,a,s,o,p,d,g)=>{let m=p;if(d&&ge.size(d.dims)>0){if(s===1)throw new Error("AddBiasReshape is not implemented. Please export your model with packed QKV or KV");return m=b_(e,p,d,r,s,a*o,g),m=m.reshape([r,s,a,o]),a===1||s===1?m:e.compute(si(m,Ud.perm),{inputs:[m],outputs:[-1]})[0]}else return p.dims.length===3&&(m=p.reshape([r,s,a,o])),a===1||s===1?m:e.compute(si(m,Ud.perm),{inputs:[m],outputs:[-1]})[0]},T$=(e,r)=>{let a=w_(e.inputs,r),s=e.inputs[0],o=qr(e.inputs,1),p=qr(e.inputs,2),d=qr(e.inputs,3),g=qr(e.inputs,4),m=qr(e.inputs,5),_=qr(e.inputs,6),v=qr(e.inputs,7);if(s.dims.length===5)throw new Error("Packed QKV is not implemented");if((o==null?void 0:o.dims.length)===5)throw new Error("Packed KV is not implemented");let x=o&&p&&o.dims.length===4&&p.dims.length===4,T=Vo(e,a.batchSize,a.numHeads,a.sequenceLength,a.headSize,s,d,0);if(x)return Fo(e,T,o,p,g,void 0,_,v,m,a);if(!o||!p)throw new Error("key and value must be provided");let C=Vo(e,a.batchSize,a.numHeads,a.kvSequenceLength,a.headSize,o,d,a.hiddenSize),A=Vo(e,a.batchSize,a.numHeads,a.kvSequenceLength,a.vHeadSize,p,d,2*a.hiddenSize);Fo(e,T,C,A,g,void 0,_,v,m,a)}}),$_,v_,x_,S_,yp,E$,I$,z$=Ee(()=>{ut(),ct(),Jt(),ft(),$_=e=>{if(!e||e.length<1)throw new Error("too few inputs")},v_=(e,r)=>{let a=[],s=r.numOutputs;return e[1].dims[0]>0&&(e[1].getBigInt64Array().forEach(o=>a.push(Number(o))),s=a.length),Nt({numOutputs:s,axis:r.axis,splitSizes:a})},x_=e=>`
fn calculateOutputIndex(index: u32) -> u32 {
    for (var i: u32 = 0u; i < ${e}u; i += 1u ) {
    if (index < ${Qe("uniforms.size_in_split_axis","i",e)}) {
        return i;
    }
    }
    return ${e}u;
}`,S_=e=>{let r=e.length,a=[];for(let s=0;s<r;++s){let o=e[s].setByIndices("indices","input[global_idx]");r===1?a.push(o):s===0?a.push(`if (output_number == ${s}u) { ${o} }`):s===r-1?a.push(`else { ${o} }`):a.push(`else if (output_number == ${s}) { ${o} }`)}return`
      fn writeBufferData(output_number: u32, indices: ${e[0].type.indices}, global_idx: u32) {
        ${a.join(`
`)}
      }`},yp=(e,r)=>{let a=e[0].dims,s=ge.size(a),o=e[0].dataType,p=ge.normalizeAxis(r.axis,a.length),d=new Array(r.numOutputs),g=$e("input",o,a.length),m=new Array(r.numOutputs),_=[],v=[],x=0,T=[{type:12,data:s}];for(let A=0;A<r.numOutputs;A++){x+=r.splitSizes[A],m[A]=x;let R=a.slice();R[p]=r.splitSizes[A],v.push(R),d[A]=je(`output${A}`,o,R.length),_.push({dims:v[A],dataType:e[0].dataType})}T.push({type:12,data:m},...Je(a,...v));let C=A=>`
  ${A.registerUniform("input_size","u32").registerUniform("size_in_split_axis","u32",m.length).declareVariables(g,...d)}
  ${x_(m.length)}
  ${S_(d)}

  ${A.mainStart()}
    ${A.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.input_size")}

    var indices = ${g.offsetToIndices("global_idx")};
    var index = ${g.indicesGet("indices",p)};
    let output_number = calculateOutputIndex(index);
    if (output_number != 0) {
      index -= ${Qe("uniforms.size_in_split_axis","output_number - 1u",m.length)};
      ${g.indicesSet("indices",p,"index")};
    }
    writeBufferData(output_number, indices, global_idx);
  }`;return{name:"Split",shaderCache:{hint:r.cacheKey,inputDependencies:["rank"]},getShaderSource:C,getRunData:()=>({outputs:_,dispatchGroup:{x:Math.ceil(s/64)},programUniforms:T})}},E$=(e,r)=>{$_(e.inputs);let a=e.inputs.length===1?r:v_(e.inputs,r);e.compute(yp(e.inputs,a),{inputs:[0]})},I$=e=>{let r=e.axis,a=e.splitSizes,s=e.numOutputs<0?a.length:e.numOutputs;if(s!==a.length)throw new Error("numOutputs and splitSizes length must be equal");return Nt({axis:r,numOutputs:s,splitSizes:a})}}),T_,ol,C$,A$=Ee(()=>{ut(),ct(),Jt(),ft(),T_=(e,r)=>{let[a,s,o,p]=e,{numHeads:d,rotaryEmbeddingDim:g}=r;if(a.dims.length!==3&&a.dims.length!==4)throw new Error(`Input 'x' is expected to have 3 or 4 dimensions, got ${a.dims.length}`);if(!ge.areEqual(s.dims,[])&&!ge.areEqual(s.dims,[1])&&s.dims.length!==2)throw new Error(`Input 'position_ids' is expected to have 0, 1, or 2 dimensions, got ${s.dims.length}`);if(o.dims.length!==2)throw new Error(`Input 'cos_cache' is expected to have 2 dimensions, got ${o.dims.length}`);if(p.dims.length!==2)throw new Error(`Input 'sin_cache' is expected to have 2 dimensions, got ${p.dims.length}`);if(!ge.areEqual(o.dims,p.dims))throw new Error("Inputs 'cos_cache' and 'sin_cache' are expected to have the same shape");if(g>0&&d===0)throw new Error("num_heads must be provided if rotary_embedding_dim is specified");let m=a.dims[0],_=a.dims[a.dims.length-2],v=o.dims[0],x=ge.sizeFromDimension(a.dims,1)/_,T=g===0?o.dims[1]*2:x/d;if(g>T)throw new Error("rotary_embedding_dim must be less than or equal to head_size");if(s.dims.length===2){if(m!==s.dims[0])throw new Error(`Input 'position_ids' dimension 0 should be of size batch_size, got ${s.dims[0]}`);if(_!==s.dims[1])throw new Error(`Input 'position_ids' dimension 1 should be of size sequence_length, got ${s.dims[1]}`)}if(_>v)throw new Error("Updating cos_cache and sin_cache in RotaryEmbedding is not currently supported");if(T/2!==o.dims[1]&&g/2!==o.dims[1])throw new Error(`Input 'cos_cache' dimension 1 should be same as head_size / 2 or rotary_embedding_dim / 2, got ${o.dims[1]}`)},ol=(e,r)=>{let{interleaved:a,numHeads:s,rotaryEmbeddingDim:o,scale:p}=r,d=e[0].dims[0],g=ge.sizeFromDimension(e[0].dims,1),m=e[0].dims[e[0].dims.length-2],_=g/m,v=e[2].dims[1],x=o===0?v*2:_/s,T=new Array(d,m,_/x,x-v),C=ge.computeStrides(T),A=[{type:1,data:p},{type:12,data:T},{type:12,data:C},...e[0].dims.length===3?new Array({type:12,data:[g,_,x,1]}):[],...e[0].dims.length===4?new Array({type:12,data:[g,x,m*x,1]}):[],...Je(e[0].dims,e[1].dims,e[2].dims,e[3].dims,e[0].dims)],R=H=>{let U=$e("input",e[0].dataType,e[0].dims.length),P=$e("position_ids",e[1].dataType,e[1].dims.length),F=$e("cos_cache",e[2].dataType,e[2].dims.length),G=$e("sin_cache",e[3].dataType,e[3].dims.length),K=je("output",e[0].dataType,e[0].dims.length);return H.registerUniforms([{name:"scale",type:"f32"},{name:"global_shape",type:"u32",length:T.length},{name:"global_strides",type:"u32",length:C.length},{name:"input_output_strides",type:"u32",length:C.length}]),`
        ${H.declareVariables(U,P,F,G,K)}

        ${H.mainStart(gs)}
          let half_rotary_emb_dim = uniforms.${F.name}_shape[1];
          let bsnh = global_idx / uniforms.global_strides % uniforms.global_shape;
          let size = uniforms.global_shape[0] * uniforms.global_strides[0];
          ${H.guardAgainstOutOfBoundsWorkgroupSizes("size")}

          if (bsnh[3] < half_rotary_emb_dim) {
            let position_ids_idx =
                ${P.broadcastedIndicesToOffset("bsnh.xy",je("",P.type.tensor,2))};
            let position_id =
                u32(${P.getByOffset("position_ids_idx")}) + select(0, bsnh[1], position_ids_idx == 0);
            let i = dot(bsnh, uniforms.input_output_strides) + select(0, bsnh[3], ${a});
            let j = i + select(half_rotary_emb_dim, 1, ${a});
            let re = ${U.getByOffset("i")} * ${F.get("position_id","bsnh[3]")} -
                ${U.getByOffset("j")} * ${G.get("position_id","bsnh[3]")};
            ${K.setByOffset("i","re")}
            let im = ${U.getByOffset("i")} * ${G.get("position_id","bsnh[3]")} +
                ${U.getByOffset("j")} * ${F.get("position_id","bsnh[3]")};
            ${K.setByOffset("j","im")}
          } else {
            let k = dot(bsnh, uniforms.input_output_strides) + half_rotary_emb_dim;
            ${K.setByOffset("k",U.getByOffset("k"))}
          }
        }`};return{name:"RotaryEmbedding",shaderCache:{hint:Nt({interleaved:a}).cacheKey,inputDependencies:["rank","rank","rank","rank"]},getShaderSource:R,getRunData:()=>({outputs:[{dims:e[0].dims,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(ge.size(T)/gs)},programUniforms:A})}},C$=(e,r)=>{T_(e.inputs,r),e.compute(ol(e.inputs,r))}}),k_,E_,Ld,I_,O$,T1=Ee(()=>{Jt(),ut(),Dp(),k$(),z$(),nn(),A$(),ft(),k_=(e,r)=>{if(r.doRotary&&e.length<=7)throw new Error("cos_cache and sin_cache inputs are required if do_rotary is specified");let a=e[0],s=e[1],o=e[2],p=e[3],d=e[4];if(r.doRotary!==0&&e.length<=7)throw new Error("cos_cast and sin_cache are expected if do_rotary attribute is non-zero");if(r.localWindowSize!==-1)throw new Error("Local attention is not supported");if(r.softcap!==0)throw new Error("Softcap is not supported");if(r.rotaryInterleaved!==0)throw new Error("Rotary interleaved is not supported");if(r.smoothSoftmax)throw new Error("Smooth softmax is not supported");if(a.dims.length!==3&&a.dims.length!==5)throw new Error("Input query is expected to have 3 or 5 dimensions");let g=!1,m=a.dims[0],_=a.dims[1],v=a.dims.length===3?g?a.dims[2]/3:a.dims[2]:r.numHeads*a.dims[4],x=_,T=0,C=!s||s.dims.length===0,A=Math.floor(C?v/(r.numHeads+2*r.kvNumHeads):v/r.numHeads);C&&(v=A*r.numHeads);let R=p&&p.dims.length!==0,H=d&&d.dims.length!==0;if(R&&p.dims.length===4&&p.dims[0]===m&&p.dims[1]!==r.kvNumHeads&&p.dims[2]===r.kvNumHeads&&p.dims[3]===A)throw new Error("BSNH pastKey/pastValue is not supported");if(R&&H){if(p.dims.length!==4)throw new Error('Input "past_key" is expected to have 4 dimensions');if(d.dims.length!==4)throw new Error('Input "past_value" is expected to have 4 dimensions');T=p.dims[2]}else if(R||H)throw new Error('Input "past_key" and "past_value" shall be both present or both absent');let U=1;if(s&&s.dims.length>0){if(a.dims.length!==3)throw new Error('Input "query" is expected to have 3 dimensions when key is given');if(s.dims.length<3||s.dims.length>5)throw new Error('Input "key" is expected to have 3, 4, or 5 dimensions');if(a.dims[0]!==s.dims[0])throw new Error('Input "query" and "key" shall have same dim 0 (batch size)');if(s.dims.length===3){if(a.dims[2]%s.dims[2]!==0)throw new Error('Dimension 2 of "query" should be a multiple of "key"');x=s.dims[1]}else if(s.dims.length===5){if(s.dims[2]!==r.numHeads||s.dims[3]!==2||s.dims[4]!==A)throw new Error('Expect "key" shape (batch_size, kv_sequence_length, num_heads, 2, head_size) for packed kv');if(o)throw new Error('Expect "value" be none when "key" has packed kv format.');x=s.dims[1]}else{if(s.dims[1]!==r.numHeads||s.dims[3]!==A)throw new Error('Expect "key" shape (batch_size, num_heads, kv_sequence_length, head_size) for past_key');x=s.dims[2]}}else{if(a.dims.length!==3&&a.dims.length!==5)throw new Error('Input "query" is expected to have 3 or 5 dimensions when key is empty');if(a.dims.length===5&&(a.dims[2]!==r.numHeads||a.dims[3]!==3))throw new Error('Expect "query" shape (batch_size, kv_sequence_length, num_heads, 3, head_size) for packed kv');U=3}let P=0,F=!1,G=r.kvNumHeads?A*r.kvNumHeads:v;if(o&&o.dims.length>0){if(o.dims.length!==3&&o.dims.length!==4)throw new Error('Input "value" is expected to have 3 or 4 dimensions');if(a.dims[0]!==o.dims[0])throw new Error('Input "query" and "value" shall have same dim 0 (batch_size)');if(o.dims.length===3){if(x!==o.dims[1])throw new Error('Input "key" and "value" shall have the same dim 1 (kv_sequence_length)');G=o.dims[2]}else{if(x!==o.dims[2])throw new Error('Input "past_key" and "past_value" shall have the same dim 2 (kv_sequence_length)');G=o.dims[1]*o.dims[3],F=!0}}let K=e.length>4?e[5]:void 0;if(K){if(K.dims.length===0)throw new Error("seqlens_k must be at least 1D, got scalar.");let ee=K.dims.reduce((ae,B)=>ae*B,1);if(ee!==m)throw new Error(`seqlens_k must have batch_size (${m}) elements, got ${ee}.`);for(let ae=0;ae<K.dims.length;ae++)if(K.dims[ae]!==1&&K.dims[ae]!==m)throw new Error(`seqlens_k has unexpected shape. Each dimension must be 1 or batch_size (${m}), got dims[${ae}] = ${K.dims[ae]}.`)}return{batchSize:m,sequenceLength:_,pastSequenceLength:T,kvSequenceLength:x,totalSequenceLength:-1,maxSequenceLength:-1,inputHiddenSize:0,hiddenSize:v,vHiddenSize:G,headSize:A,vHeadSize:Math.floor(G/r.kvNumHeads),numHeads:r.numHeads,kvNumHeads:r.kvNumHeads,nReps:r.numHeads/r.kvNumHeads,pastPresentShareBuffer:!1,maskType:P,scale:r.scale,broadcastResPosBias:!1,passPastInKv:F,qkvFormat:U}},E_=Nt({perm:[0,2,1,3]}),Ld=(e,r,a)=>{let s=r,o=a.kvNumHeads;return r.dims.length===3&&a.kvSequenceLength!==0&&(s=r.reshape([a.batchSize,a.kvSequenceLength,o,a.headSize]),s=e.compute(si(s,E_.perm),{inputs:[s],outputs:[-1]})[0]),s},I_=(e,r,a,s)=>{let o=7,p=["type","type"],d=[e*r],g=e*r,m=[{type:12,data:g},{type:12,data:r},{type:12,data:e}],_=v=>{let x=$e("seq_lens",a.dataType,a.dims),T=$e("total_seq_lens",s.dataType,s.dims),C=je("pos_ids",o,d),A=[{name:"output_size",type:"u32"},{name:"sequence_length",type:"u32"},{name:"batch_size",type:"u32"}];return`
  ${v.registerUniforms(A).declareVariables(x,T,C)}
  ${v.mainStart()}
    ${v.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
    let total_sequence_length = u32(${T.getByOffset("0")});
    let is_subsequent_prompt = uniforms.sequence_length > 1 && uniforms.sequence_length != total_sequence_length;
    let is_first_prompt = !is_subsequent_prompt && uniforms.sequence_length == total_sequence_length;
    let batch_idx = global_idx / uniforms.sequence_length;
    let sequence_idx = i32(global_idx % uniforms.sequence_length);
    var pos_id: i32 = 0;
    let seqlen = ${x.getByOffset("batch_idx")};
    let total_seqlen = seqlen + 1;
    if (is_first_prompt) {
      if (sequence_idx < total_seqlen) {
        pos_id = sequence_idx;
      } else {
        pos_id = 1;
      }
      ${C.setByOffset("global_idx","pos_id")}
    } else if (is_subsequent_prompt) {
      let past_seqlen = total_seqlen - i32(uniforms.sequence_length);
      if (past_seqlen + sequence_idx < total_seqlen) {
        pos_id = past_seqlen + sequence_idx;
      } else {
        pos_id = 1;
      }
      ${C.setByOffset("global_idx","pos_id")}
    } else if (global_idx < uniforms.batch_size) {
      ${C.setByOffset("global_idx","seqlen")}
    };
  }
  `};return{name:"GeneratePositionIds",shaderCache:{hint:`${e};${r}`,inputDependencies:p},getRunData:()=>({outputs:[{dims:d,dataType:o}],dispatchGroup:{x:Math.ceil(g/64)},programUniforms:m}),getShaderSource:_}},O$=(e,r)=>{var G;let a=k_(e.inputs,r);if(e.inputs[0].dims.length===5)throw new Error("Packed QKV is not implemented");if(((G=e.inputs[1])==null?void 0:G.dims.length)===5)throw new Error("Packed KV is not implemented");let s=e.inputs[0],o=e.inputs[1]&&e.inputs[1].dims.length>0?e.inputs[1]:void 0,p=e.inputs[2]&&e.inputs[2].dims.length>0?e.inputs[2]:void 0,d=e.inputs[3]&&e.inputs[3].dims.length!==0?e.inputs[3]:void 0,g=e.inputs[4]&&e.inputs[4].dims.length!==0?e.inputs[4]:void 0,m=e.inputs.length>4?e.inputs[5]:void 0,_=e.inputs.length>5?e.inputs[6]:void 0,v=a.kvNumHeads?a.kvNumHeads:a.numHeads,x=Nt({axis:2,numOutputs:3,splitSizes:[a.numHeads*a.headSize,v*a.headSize,v*a.headSize]}),[T,C,A]=!o&&!p?e.compute(yp([s],x),{inputs:[s],outputs:[-1,-1,-1]}):[s,o,p],R,H;if(r.doRotary){let K=e.compute(I_(a.batchSize,a.sequenceLength,m,_),{inputs:[m,_],outputs:[-1]})[0],ee=e.inputs[7],ae=e.inputs[8],B=Nt({interleaved:r.rotaryInterleaved!==0,numHeads:a.numHeads,rotaryEmbeddingDim:0,scale:r.scale}),me=[T,K,ee,ae],_e=[-1];R=e.compute(ol(me,B),{inputs:me,outputs:_e})[0],me.splice(0,1,C);let Re=Nt({interleaved:r.rotaryInterleaved!==0,numHeads:a.kvNumHeads,rotaryEmbeddingDim:0,scale:r.scale});H=e.compute(ol(me,Re),{inputs:me,outputs:_e})[0]}let U=Vo(e,a.batchSize,a.numHeads,a.sequenceLength,a.headSize,r.doRotary?R:T,void 0,0),P=Ld(e,r.doRotary?H:C,a),F=Ld(e,A,a);Fo(e,U,P,F,void 0,void 0,d,g,void 0,a,m,_)}}),qd,z_,C_,R$,k1=Ee(()=>{ut(),ct(),nn(),ft(),qd=(e,r,a,s,o,p,d,g)=>{let m=Yt(p),_=m===1?"f32":`vec${m}f`,v=m===1?"vec2f":`mat2x${m}f`,x=o*d,T=64;x===1&&(T=256);let C=[o,d,p/m],A=[o,d,2],R=["rank","type","type"],H=[];H.push(...Je(C,A));let U=P=>{let F=$e("x",r.dataType,3,m),G=$e("scale",a.dataType,a.dims),K=$e("bias",s.dataType,s.dims),ee=je("output",1,3,2),ae=[F,G,K,ee];return`
  var<workgroup> workgroup_shared : array<${v}, ${T}>;
  const workgroup_size = ${T}u;
  ${P.declareVariables(...ae)}
  ${P.mainStart(T)}
    let batch = workgroup_index / uniforms.x_shape[1];
    let channel = workgroup_index % uniforms.x_shape[1];
    let hight = uniforms.x_shape[2];
    // initialize workgroup memory
    var sum = ${_}(0);
    var squared_sum = ${_}(0);
    for (var h = local_idx; h < hight; h += workgroup_size) {
      let value = ${_}(${F.get("batch","channel","h")});
      sum += value;
      squared_sum += value * value;
    }
    workgroup_shared[local_idx] = ${v}(sum, squared_sum);
    workgroupBarrier();

    for (var currSize = workgroup_size >> 1;  currSize > 0; currSize = currSize >> 1) {
      if (local_idx < currSize) {
        workgroup_shared[local_idx] = workgroup_shared[local_idx] + workgroup_shared[local_idx + currSize];
      }
      workgroupBarrier();
    }
    if (local_idx == 0) {
      let sum_final = ${an("workgroup_shared[0][0]",m)} / f32(hight * ${m});
      let squared_sum_final = ${an("workgroup_shared[0][1]",m)} / f32(hight * ${m});

      let inv_std_dev = inverseSqrt(squared_sum_final - sum_final * sum_final + f32(${g}));
      let channel_scale = inv_std_dev * f32(scale[channel]);
      let channel_shift = f32(bias[channel]) - sum_final * channel_scale;
      output[workgroup_index] = vec2f(channel_scale, channel_shift);
    }
  }`};return e.compute({name:"InstanceNormComputeChannelScaleShift",shaderCache:{hint:`${m};${g};${T}`,inputDependencies:R},getRunData:()=>({outputs:[{dims:A,dataType:1}],dispatchGroup:{x},programUniforms:H}),getShaderSource:U},{inputs:[r,a,s],outputs:[-1]})[0]},z_=(e,r,a)=>{let s=r[0].dims,o=s,p=2,d=s[0],g=s[1],m=ge.sizeFromDimension(s,p),_=Yt(m),v=ge.size(o)/_,x=qd(e,r[0],r[1],r[2],d,m,g,a.epsilon),T=[d,g,m/_],C=[d,g],A=["type","none"],R=H=>{let U=$e("x",r[0].dataType,T.length,_),P=$e("scale_shift",1,C.length,2),F=je("output",r[0].dataType,T.length,_),G=[U,P,F];return`
  ${H.registerUniform("output_size","u32").declareVariables(...G)}
  ${H.mainStart()}
  ${H.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
      let outputIndices = ${F.offsetToIndices("global_idx")};
      let batch = outputIndices[0];
      let channel = outputIndices[1];
      let scale_shift = ${P.getByIndices("vec2<u32>(batch, channel)")};
      let value = ${U.getByOffset("global_idx")} * ${F.type.value}(scale_shift.x) + ${F.type.value}(scale_shift.y);
      ${F.setByOffset("global_idx","value")};
  }`};e.compute({name:"InstanceNormalization",shaderCache:{hint:`${_}`,inputDependencies:A},getRunData:()=>({outputs:[{dims:o,dataType:r[0].dataType}],dispatchGroup:{x:Math.ceil(v/64)},programUniforms:[{type:12,data:v},...Je(T,C,T)]}),getShaderSource:R},{inputs:[r[0],x]})},C_=(e,r,a)=>{let s=r[0].dims,o=s,p=s[0],d=s[s.length-1],g=ge.sizeFromDimension(s,1)/d,m=Yt(d),_=ge.size(o)/m,v=[{type:12,data:g},{type:12,data:Math.floor(d/m)}],x=["type","type"],T=!1,C=[0,s.length-1];for(let U=0;U<s.length-2;U++)T=T||s[U+1]!==1,C.push(U+1);T=T&&s[s.length-1]!==1;let A=T?e.compute(si(e.inputs[0],C),{inputs:[e.inputs[0]],outputs:[-1]})[0]:e.inputs[0].reshape(Array.from({length:s.length},(U,P)=>s[C[P]])),R=qd(e,A,r[1],r[2],p,g,d,a.epsilon),H=U=>{let P=yr(r[0].dataType),F=m===1?"vec2f":`mat${m}x2f`,G=ae=>{let B=ae===0?"x":"y",me=m===1?"f32":`vec${m}f`;switch(m){case 1:return`${P}(${me}(scale.${B}))`;case 2:return`vec2<${P}>(${me}(scale[0].${B}, scale[1].${B}))`;case 4:return`vec4<${P}>(${me}(scale[0].${B}, scale[1].${B}, scale[2].${B}, scale[3].${B}))`;default:throw new Error(`Not supported compoents ${m}`)}},K=$e("input",r[0].dataType,r[0].dims,m),ee=je("output",r[0].dataType,o,m);return`
  @group(0) @binding(0) var<storage, read> input : array<${K.type.storage}>;
  @group(0) @binding(1) var<storage, read> scale_input : array<${F}>;
  @group(0) @binding(2) var<storage, read_write> output : array<${ee.type.storage}>;
  struct Uniforms {H: u32, C : u32};
  @group(0) @binding(3) var<uniform> uniforms: Uniforms;

  ${U.mainStart()}
    let current_image_number = global_idx / (uniforms.C * uniforms.H);
    let current_channel_number = global_idx % uniforms.C;

    let scale_offset = current_image_number * uniforms.C + current_channel_number;
    let scale = scale_input[scale_offset];
    output[global_idx] = fma(input[global_idx], ${G(0)}, ${G(1)});
  }`};e.compute({name:"InstanceNormalizationNHWC",shaderCache:{hint:`${m}`,inputDependencies:x},getRunData:()=>({outputs:[{dims:o,dataType:r[0].dataType}],dispatchGroup:{x:Math.ceil(_/64)},programUniforms:v}),getShaderSource:H},{inputs:[r[0],R]})},R$=(e,r)=>{r.format==="NHWC"?C_(e,e.inputs,r):z_(e,e.inputs,r)}}),A_,O_,B$,E1=Ee(()=>{ut(),ct(),ft(),A_=e=>{if(!e||e.length<2)throw new Error("layerNorm requires at least 2 inputs.")},O_=(e,r,a)=>{let s=r.simplified,o=e[0].dims,p=e[1],d=!s&&e[2],g=o,m=ge.normalizeAxis(r.axis,o.length),_=ge.sizeToDimension(o,m),v=ge.sizeFromDimension(o,m),x=ge.size(p.dims),T=d?ge.size(d.dims):0;if(x!==v||d&&T!==v)throw new Error(`Size of X.shape()[axis:] == ${v}.
       Size of scale and bias (if provided) must match this.
       Got scale size of ${x} and bias size of ${T}`);let C=[];for(let K=0;K<o.length;++K)K<m?C.push(o[K]):C.push(1);let A=Yt(v),R=["type","type"],H=[{type:12,data:_},{type:1,data:v},{type:12,data:Math.floor(v/A)},{type:1,data:r.epsilon}];d&&R.push("type");let U=a>1,P=a>2,F=K=>{let ee=yr(e[0].dataType),ae=[$e("x",e[0].dataType,e[0].dims,A),$e("scale",p.dataType,p.dims,A)];d&&ae.push($e("bias",d.dataType,d.dims,A)),ae.push(je("output",e[0].dataType,g,A)),U&&ae.push(je("mean_data_output",1,C)),P&&ae.push(je("inv_std_output",1,C));let B=[{name:"norm_count",type:"u32"},{name:"norm_size",type:"f32"},{name:"norm_size_vectorized",type:"u32"},{name:"epsilon",type:"f32"}];return`
  ${K.registerUniforms(B).declareVariables(...ae)}
  ${K.mainStart()}
    ${K.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.norm_count")}
    let offset = global_idx * uniforms.norm_size_vectorized;
    var mean_vector = ${up("f32",A)};
    var mean_square_vector = ${up("f32",A)};

    for (var h: u32 = 0u; h < uniforms.norm_size_vectorized; h++) {
      let value = ${fs(ee,A,"x[h + offset]")};
      mean_vector += value;
      mean_square_vector += value * value;
    }
    let mean = ${an("mean_vector",A)} / uniforms.norm_size;
    let inv_std_dev = inverseSqrt(${an("mean_square_vector",A)} / uniforms.norm_size ${s?"":"- mean * mean"} + uniforms.epsilon);

    for (var j: u32 = 0; j < uniforms.norm_size_vectorized; j++) {
      let f32input = ${fs(ee,A,"x[j + offset]")};
      let f32scale = ${fs(ee,A,"scale[j]")};
      output[j + offset] = ${ae[0].type.value}((f32input ${s?"":"- mean"}) * inv_std_dev * f32scale
        ${d?`+ ${fs(ee,A,"bias[j]")}`:""}
      );
    }

    ${U?"mean_data_output[global_idx] = mean":""};
    ${P?"inv_std_output[global_idx] = inv_std_dev":""};
  }`},G=[{dims:g,dataType:e[0].dataType}];return U&&G.push({dims:C,dataType:1}),P&&G.push({dims:C,dataType:1}),{name:"LayerNormalization",shaderCache:{hint:`${A};${a};${s}`,inputDependencies:R},getRunData:()=>({outputs:G,dispatchGroup:{x:Math.ceil(_/64)},programUniforms:H}),getShaderSource:F}},B$=(e,r)=>{A_(e.inputs),e.compute(O_(e.inputs,r,e.outputCount))}}),R_,M$,I1=Ee(()=>{ct(),qp(),Vp(),R_=e=>{if(!e||e.length!==2)throw new Error("MatMul requires 2 inputs.");if(e[0].dims[e[0].dims.length-1]!==e[1].dims[e[1].dims.length-2])throw new Error("shared dimension does not match.")},M$=e=>{R_(e.inputs);let r=ms.calcShape(e.inputs[0].dims,e.inputs[1].dims,!0);if(!r)throw new Error("Can't use matmul on the given tensors");let a=r[r.length-1],s=e.inputs[0].dims[e.inputs[0].dims.length-1];if(a<8&&s<8)e.compute(Lp(e.inputs,{activation:""},r));else{let o=r[r.length-2],p=ge.size(e.inputs[0].dims.slice(0,-2)),d=ge.size(e.inputs[1].dims.slice(0,-2));if(p!==1&&o===1&&d===1){let g=e.inputs[0].reshape([1,p,s]),m=e.inputs[1].reshape([1,s,a]),_=[1,p,a],v=[g,m];e.compute(sl(v,{activation:""},r,_),{inputs:v})}else e.compute(sl(e.inputs,{activation:""},r))}}}),B_,M_,D_,D$,N$,z1=Ee(()=>{ut(),ct(),Jt(),ft(),B_=(e,r)=>{if(e.length<3||e.length>4)throw new Error("MatMulNBits requires 3 or 4 inputs");let a=e[0],s=a.dims.length;if(a.dims[s-1]!==r.k)throw new Error("The last dim of input shape does not match the k value");let o=Math.floor((r.k+r.blockSize-1)/r.blockSize),p=r.blockSize/8*r.bits,d=e[1];if(!ge.areEqual(d.dims,[r.n,o,p]))throw new Error("The second inputs must be 3D tensor with shape N X nBlocksPerCol X blobSize");let g=e[2].dims;if(ge.size(g)!==r.n*o)throw new Error("scales input size error.");if(e.length===4){let m=e[3].dims,_=r.n*(r.bits===8?o:Math.floor((o*r.bits+7)/8));if(ge.size(m)!==_)throw new Error("zeroPoints input size error.")}},M_=(e,r)=>{let a=e[0].dims,s=a.length,o=a[s-2],p=r.k,d=r.n,g=a.slice(0,s-2),m=ge.size(g),_=e[1].dims[2]/4,v=e[0].dataType,x=Yt(r.k),T=Yt(_),C=Yt(d),A=g.concat([o,d]),R=o>1&&d/C%2===0?2:1,H=ge.size(A)/C/R,U=64,P=[],F=[m,o,p/x],G=ge.convertShape(e[1].dims).slice();G.splice(-1,1,_/T),P.push(...Je(F)),P.push(...Je(G)),P.push(...Je(e[2].dims)),e.length===4&&P.push(...Je(ge.convertShape(e[3].dims)));let K=[m,o,d/C];P.push(...Je(K));let ee=ae=>{let B=F.length,me=$e("a",e[0].dataType,B,x),_e=$e("b",12,G.length,T),Re=$e("scales",e[2].dataType,e[2].dims.length),Ue=[me,_e,Re],Me=e.length===4?$e("zero_points",12,e[3].dims.length):void 0;Me&&Ue.push(Me);let pe=K.length,qe=je("output",e[0].dataType,pe,C),Ve=yr(e[0].dataType),ze=(()=>{switch(x){case 1:return`array<${Ve}, 8>`;case 2:return`mat4x2<${Ve}>`;case 4:return`mat2x4<${Ve}>`;default:throw new Error(`${x}-component is not supported.`)}})(),ht=Math.floor(32/r.bits),Ce=Math.floor(ht/8),nt=()=>{let We="";for(let Ie=0;Ie<Ce;Ie++){let $t=Ie*r.bits*4,_r=$t+r.bits;We+=`
          // reuse a data (pass ${Ie})
            var input_offset${Ie>0?Ie:""} = ${Ie===0?me.indicesToOffset(`${me.type.indices}(batch, row, word_offset)`):"input_offset"};
            var a_data${Ie>0?Ie:""}: ${ze};
            for (var j${Ie>0?Ie:""}: u32 = 0; j${Ie>0?Ie:""} < ${8/x}; j${Ie>0?Ie:""}++) {
              a_data${Ie>0?Ie:""}[j${Ie>0?Ie:""}] = ${me.getByOffset(`input_offset${Ie>0?Ie:""}`)};
              input_offset${Ie>0?Ie:""}++;
            }
          `;for(let jt=0;jt<C*R;jt++)We+=`
            b_value = ${T===1?`b${jt}_data`:`b${jt}_data[i]`};
            ${r.bits===2?`{
              let half_word = b_value >> ${Ie*16}u;
              let byte_lo = half_word & 0xFFu;
              let byte_hi = (half_word >> 8u) & 0xFFu;
              let spread_word = (byte_lo & 0xFu) | ((byte_lo >> 4u) << 8u) | ((byte_hi & 0xFu) << 16u) | ((byte_hi >> 4u) << 24u);
              b_value_lower = unpack4xU8(spread_word & b_mask);
              b_value_upper = unpack4xU8((spread_word >> 2u) & b_mask);
            }`:`b_value_lower = unpack4xU8((b_value >> ${$t}u) & b_mask);
            b_value_upper = unpack4xU8((b_value >> ${_r}u) & b_mask);`}
            b_quantized_values = ${ze}(${Array.from({length:4},(_t,er)=>`${Ve}(b_value_lower[${er}]), ${Ve}(b_value_upper[${er}])`).join(", ")});
            b_dequantized_values = ${x===1?`${ze}(${Array.from({length:8},(_t,er)=>`(b_quantized_values[${er}] - ${Me?`zero_point${jt}`:"zero_point"}) * scale${jt}`).join(", ")});`:`(b_quantized_values - ${ze}(${Array(8).fill(`${Me?`zero_point${jt}`:"zero_point"}`).join(",")})) * scale${jt};`};
            workgroup_shared[local_id.x * ${R} + ${Math.floor(jt/C)}]${C>1?`[${jt%C}]`:""} += ${Array.from({length:8/x},(_t,er)=>`${x===1?`a_data${Ie>0?Ie:""}[${er}] * b_dequantized_values[${er}]`:`dot(a_data${Ie>0?Ie:""}[${er}], b_dequantized_values[${er}])`}`).join(" + ")};
          `}return We},Te=()=>{let We=`
            var col_index = col * ${C};
            ${Me?`
            let zero_point_values_per_byte: u32 = ${Math.floor(8/r.bits)}u;
            let zero_point_bytes_per_col = (nBlocksPerCol + zero_point_values_per_byte - 1u) / zero_point_values_per_byte;
            var zero_point_byte_count: u32;
            var zero_point_word_index: u32;
            var zero_point_byte_offset: u32;
            let zero_point_sub_offset: u32 = block % zero_point_values_per_byte;
            var zero_point_bits_offset: u32;
            var zero_point_word: u32;`:`
            // The default zero point is ${Math.pow(2,r.bits-1)} for unsigned ${r.bits}-bit quantization.
            let zero_point = ${Ve}(${Math.pow(2,r.bits-1).toFixed(1)});`}
            `;for(let Ie=0;Ie<C*R;Ie++)We+=`
            let scale${Ie} = ${Re.getByOffset("col_index * nBlocksPerCol + block")};
            ${Me?`
            zero_point_byte_count = col_index * zero_point_bytes_per_col + (block / zero_point_values_per_byte);
            zero_point_word_index = zero_point_byte_count >> 0x2u;
            zero_point_byte_offset = zero_point_byte_count & 0x3u;
            zero_point_bits_offset = (zero_point_byte_offset << 3) + (zero_point_sub_offset * ${r.bits}u);
            zero_point_word = ${Me.getByOffset("zero_point_word_index")} >> zero_point_bits_offset;
            let zero_point${Ie} = ${Ve}((zero_point_word) & ${r.bits===2?"0x3u":"0xFu"});`:""}
            col_index += 1;`;return We},Be=()=>{let We=`col_index = col * ${C};`;for(let Ie=0;Ie<C*R;Ie++)We+=`
            let b${Ie}_data = ${_e.getByIndices(`${_e.type.indices}(col_index, block, word)`)};
            col_index += 1;`;return We+=`
            var b_value: u32;
            let b_mask: u32 = ${r.bits===2?"0x03030303u":"0x0F0F0F0Fu"};
            var b_value_lower: vec4<u32>;
            var b_value_upper: vec4<u32>;
            var b_quantized_values: ${ze};
            var b_dequantized_values: ${ze};`,We};return`
        var<workgroup> workgroup_shared: array<${qe.type.value}, ${R*U}>;
        ${ae.declareVariables(...Ue,qe)}
        ${ae.mainStart([U,1,1])}
          let output_indices = ${qe.offsetToIndices(`(global_idx / ${U}) * ${R}`)};
          let col = output_indices[2];
          let row = output_indices[1];
          let batch = output_indices[0];
          let nBlocksPerCol = uniforms.b_shape[1];

          for (var block = local_id.x; block < nBlocksPerCol; block += ${U}) {
            //process one block
            var word_offset: u32 = block * ${r.blockSize/x};
            ${Te()}
            for (var word: u32 = 0; word < ${_}; word += ${T}) {
              ${Be()}
              for (var i: u32 = 0; i < ${T}; i++) {
                ${nt()}
                word_offset += ${ht/x};
              }
            }
          }
          workgroupBarrier();

          if (local_id.x < ${R}) {
            var output_value: ${qe.type.value} = ${qe.type.value}(0);
            var workgroup_shared_offset: u32 = local_id.x;
            for (var b: u32 = 0u; b < ${U}u; b++) {
              output_value += workgroup_shared[workgroup_shared_offset];
              workgroup_shared_offset += ${R};
            }
            ${qe.setByIndices(`${qe.type.indices}(batch, row, col + local_id.x)`,"output_value")};
          }
        }`};return{name:"MatMulNBits",shaderCache:{hint:`${r.blockSize};${r.bits};${x};${T};${C};${R};${U}`,inputDependencies:Array(e.length).fill("rank")},getRunData:()=>({outputs:[{dims:A,dataType:v}],dispatchGroup:{x:H},programUniforms:P}),getShaderSource:ee}},D_=(e,r)=>{let a=e[0].dims,s=a.length,o=a[s-2],p=r.k,d=r.n,g=a.slice(0,s-2),m=ge.size(g),_=e[1].dims[2]/4,v=e[0].dataType,x=Yt(r.k),T=Yt(_),C=g.concat([o,d]),A=128,R=d%8===0?8:d%4===0?4:1,H=A/R,U=Math.floor(32/r.bits),P=H*T*U,F=P/x,G=P/r.blockSize,K=ge.size(C)/R,ee=[],ae=[m,o,p/x],B=ge.convertShape(e[1].dims).slice();B.splice(-1,1,_/T),ee.push(...Je(ae)),ee.push(...Je(B)),ee.push(...Je(e[2].dims)),e.length===4&&ee.push(...Je(ge.convertShape(e[3].dims)));let me=[m,o,d];ee.push(...Je(me));let _e=Re=>{let Ue=ae.length,Me=$e("a",e[0].dataType,Ue,x),pe=$e("b",12,B.length,T),qe=$e("scales",e[2].dataType,e[2].dims.length),Ve=[Me,pe,qe],ze=e.length===4?$e("zero_points",12,e[3].dims.length):void 0;ze&&Ve.push(ze);let ht=me.length,Ce=je("output",e[0].dataType,ht),nt=yr(e[0].dataType),Te=()=>{switch(x){case 1:return`
          let a_data0 = vec4<${nt}>(sub_a[word_offset], sub_a[word_offset + 1], sub_a[word_offset + 2], sub_a[word_offset + 3]);
          let a_data1 = vec4<${nt}>(sub_a[word_offset + 4], sub_a[word_offset + 5], sub_a[word_offset + 6], sub_a[word_offset + 7]);`;case 2:return`
          let a_data0 = vec4<${nt}>(sub_a[word_offset], sub_a[word_offset + 1]);
          let a_data1 = vec4<${nt}>(sub_a[word_offset + 2], sub_a[word_offset + 3]);`;case 4:return`
          let a_data0 = sub_a[word_offset];
          let a_data1 = sub_a[word_offset + 1];`;default:throw new Error(`${x}-component is not supported.`)}};return`
        var<workgroup> sub_a: array<${Me.type.value}, ${F}>;
        var<workgroup> inter_results: array<array<${Ce.type.value}, ${H}>, ${R}>;
        ${Re.declareVariables(...Ve,Ce)}
        ${Re.mainStart([H,R,1])}
          let output_indices = ${Ce.offsetToIndices(`workgroup_index * ${R}`)};
          let col = output_indices[2];
          let row = output_indices[1];
          let batch = output_indices[0];
          let n_blocks_per_col = uniforms.b_shape[1];
          let num_tiles =  (n_blocks_per_col - 1) / ${G} + 1;

          // Loop over shared dimension.
          for (var tile: u32 = 0; tile < num_tiles; tile += 1) {
            let a_col_start = tile * ${F};
            // load one tile A data into shared memory.
            for (var a_offset = local_idx; a_offset < ${F}; a_offset += ${A})
            {
              let a_col = a_col_start + a_offset;
              if (a_col < uniforms.a_shape[2])
              {
                sub_a[a_offset] = ${Me.getByIndices(`${Me.type.indices}(batch, row, a_col)`)};
              } else {
                sub_a[a_offset] = ${Me.type.value}(0);
              }
            }
            workgroupBarrier();

            // each thread process one block
            let b_row = col + local_id.y;
            let block = tile * ${G} + local_id.x;
            ${ze?`
            let zero_point_values_per_byte: u32 = ${Math.floor(8/r.bits)}u;
            let zero_point_bytes_per_col = (n_blocks_per_col + zero_point_values_per_byte - 1u) / zero_point_values_per_byte;
            let zero_point_byte_count = b_row * zero_point_bytes_per_col + (block / zero_point_values_per_byte);
            let zero_point_word_index = zero_point_byte_count >> 0x2u;
            let zero_point_byte_offset = zero_point_byte_count & 0x3u;
            let zero_point_sub_offset: u32 = block % zero_point_values_per_byte;
            let zero_point_bits_offset = (zero_point_byte_offset << 3) + (zero_point_sub_offset * ${r.bits}u);
            let zero_point_word = ${ze.getByOffset("zero_point_word_index")} >> zero_point_bits_offset;
            let zero_point = ${nt}((zero_point_word) & ${r.bits===2?"0x3u":"0xFu"});`:`
            // The default zero point is ${Math.pow(2,r.bits-1)} for unsigned ${r.bits}-bit quantization.
            let zero_point = ${nt}(${Math.pow(2,r.bits-1).toFixed(1)});`}
            let scale = ${qe.getByOffset("b_row * n_blocks_per_col + block")};
            let b_data = ${pe.getByIndices(`${pe.type.indices}(b_row, block, 0)`)};
            var word_offset = local_id.x * ${r.blockSize/x};
            for (var i: u32 = 0; i < ${T}; i++) {
              let b_value = ${T===1?"b_data":"b_data[i]"};
              ${(()=>{let Be=Math.floor(U/8),We="";for(let Ie=0;Ie<Be;Ie++){let $t=Ie*r.bits*4,_r=$t+r.bits;We+=`
              ${Te()}
              {${r.bits===2?`
                let half_word = b_value >> ${Ie*16}u;
                let byte_lo = half_word & 0xFFu;
                let byte_hi = (half_word >> 8u) & 0xFFu;
                let spread_word = (byte_lo & 0xFu) | ((byte_lo >> 4u) << 8u) | ((byte_hi & 0xFu) << 16u) | ((byte_hi >> 4u) << 24u);
                let b_value_lower = unpack4xU8(spread_word & 0x03030303u);
                let b_value_upper = unpack4xU8((spread_word >> 2u) & 0x03030303u);`:`
                let b_value_lower = unpack4xU8((b_value >> ${$t}u) & 0x0F0F0F0Fu);
                let b_value_upper = unpack4xU8((b_value >> ${_r}u) & 0x0F0F0F0Fu);`}
                let b_quantized_values = mat2x4<${nt}>(${Array.from({length:4},(jt,_t)=>`${nt}(b_value_lower[${_t}]), ${nt}(b_value_upper[${_t}])`).join(", ")});
                let b_dequantized_values = (b_quantized_values - mat2x4<${nt}>(${Array(8).fill("zero_point").join(",")})) * scale;
                inter_results[local_id.y][local_id.x] += ${Array.from({length:2},(jt,_t)=>`${`dot(a_data${_t}, b_dequantized_values[${_t}])`}`).join(" + ")};
              }
              word_offset += ${8/x};`}return We})()}
            }
            workgroupBarrier();
          }

          if (local_idx < ${R}) {
            var output_value: ${Ce.type.value} = ${Ce.type.value}(0);
            for (var b = 0u; b < ${H}; b++) {
              output_value += inter_results[local_idx][b];
            }
            if (col + local_idx < uniforms.output_shape[2])
            {
              ${Ce.setByIndices(`${Ce.type.indices}(batch, row, col + local_idx)`,"output_value")}
            }
          }
        }`};return{name:"BlockwiseMatMulNBits32",shaderCache:{hint:`${r.blockSize};${x};${T};${H};${R}`,inputDependencies:Array(e.length).fill("rank")},getRunData:()=>({outputs:[{dims:C,dataType:v}],dispatchGroup:{x:K},programUniforms:ee}),getShaderSource:_e}},D$=(e,r)=>{B_(e.inputs,r),r.blockSize===32&&e.adapterInfo.isVendor("intel")&&e.adapterInfo.isArchitecture("gen-12lp")?e.compute(D_(e.inputs,r)):e.compute(M_(e.inputs,r))},N$=e=>Nt(e)}),N_,P_,U_,L_,q_,V_,W_,G_,P$,C1=Ee(()=>{ut(),ct(),ft(),N_=e=>{if(!e||e.length<1)throw new Error("Too few inputs");if(e[0].dataType!==1&&e[0].dataType!==10)throw new Error("Input type must be float or float16.");if(e.length>=2){let r=e[0].dims.length*2===e[1].dims[0];if(e.length===4&&(r=e[3].dims[0]*2===e[1].dims[0]),!r)throw new Error("The pads should be a 1D tensor of shape [2 * input_rank] or [2 * num_axes].")}},P_=(e,r,a)=>{let s="";for(let o=r-1;o>=0;--o)s+=`
            k = i32(${e.indicesGet("indices",o)}) - ${Qe("uniforms.pads",o,a)};
            if (k < 0) {
              break;
            }
            if (k >= i32(${Qe("uniforms.x_shape",o,r)})) {
              break;
            }
            offset += k * i32(${Qe("uniforms.x_strides",o,r)});
        `;return`
          value = ${e.type.value}(uniforms.constant_value);
          for (var i = 0; i < 1; i++) {
            var offset = 0;
            var k = 0;
            ${s}
            value = x[offset];
          }
      `},U_=(e,r,a)=>{let s="";for(let o=r-1;o>=0;--o)s+=`
                k = i32(${e.indicesGet("indices",o)}) - ${Qe("uniforms.pads",o,a)};
                if (k < 0) {
                  k = -k;
                }
                {
                  let _2n_1 = 2 * (i32(${Qe("uniforms.x_shape",o,r)}) - 1);
                  k = k % _2n_1;
                  if(k >= i32(${Qe("uniforms.x_shape",o,r)})) {
                    k = _2n_1 - k;
                  }
                }
                offset += k * i32(${Qe("uniforms.x_strides",o,r)});
            `;return`
              var offset = 0;
              var k = 0;
              ${s}
              value = x[offset];
          `},L_=(e,r,a)=>{let s="";for(let o=r-1;o>=0;--o)s+=`
                k = i32(${e.indicesGet("indices",o)}) - ${Qe("uniforms.pads",o,a)};
                if (k < 0) {
                  k = 0;
                }
                if (k >= i32(${Qe("uniforms.x_shape",o,r)})) {
                  k = i32(${Qe("uniforms.x_shape",o,r)}) - 1;
                }
                offset += k * i32(${Qe("uniforms.x_strides",o,r)});
            `;return`
              var offset = 0;
              var k = 0;
              ${s}
              value = x[offset];
          `},q_=(e,r,a)=>{let s="";for(let o=r-1;o>=0;--o)s+=`
                k = i32(${e.indicesGet("indices",o)}) - ${Qe("uniforms.pads",o,a)};
                if (k < 0)  {
                  k += i32(${Qe("uniforms.x_shape",o,r)}]);
                }
                if (k >= i32(${Qe("uniforms.x_shape",o,r)})) {
                  k -= i32(${Qe("uniforms.x_shape",o,r)});
                }
                offset += k * i32(${Qe("uniforms.x_strides",o,r)});
            `;return`
              var offset = 0;
              var k = 0;
              ${s}
              value = x[offset];
          `},V_=(e,r,a)=>{switch(a.mode){case 0:return P_(e,r,a.pads.length);case 1:return U_(e,r,a.pads.length);case 2:return L_(e,r,a.pads.length);case 3:return q_(e,r,a.pads.length);default:throw new Error("Invalid mode")}},W_=(e,r)=>{let a=ge.padShape(e[0].dims.slice(),r.pads),s=e[0].dims,o=ge.size(a),p=[{type:12,data:o},{type:6,data:r.pads}],d=e.length>=3&&e[2].data;r.mode===0&&p.push({type:d?e[2].dataType:1,data:r.value}),p.push(...Je(e[0].dims,a));let g=["rank"],m=_=>{let v=je("output",e[0].dataType,a.length),x=$e("x",e[0].dataType,s.length),T=x.type.value,C=V_(v,s.length,r),A=[{name:"output_size",type:"u32"},{name:"pads",type:"i32",length:r.pads.length}];return r.mode===0&&A.push({name:"constant_value",type:d?T:"f32"}),`
            ${_.registerUniforms(A).declareVariables(x,v)}
            ${_.mainStart()}
            ${_.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}

            let indices = ${v.offsetToIndices("global_idx")};

            var value = ${T}(0);
            ${C}
            output[global_idx] = value;
        }`};return{name:"Pad",shaderCache:{hint:`${r.mode}${d}`,inputDependencies:g},getRunData:()=>({outputs:[{dims:a,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(ge.size(a)/64)},programUniforms:p}),getShaderSource:m}},G_=(e,r)=>{if(e.length>1){let a=e[1].getBigInt64Array(),s=e.length>=3&&e[2].data?e[2].dataType===10?e[2].getUint16Array()[0]:e[2].getFloat32Array()[0]:0,o=e[0].dims.length,p=new Int32Array(2*o).fill(0);if(e.length>=4){let g=e[3].getBigInt64Array();for(let m=0;m<g.length;m++)p[Number(g[m])]=Number(a[m]),p[Number(g[m])+o]=Number(a[m+g.length])}else a.forEach((g,m)=>p[Number(m)]=Number(g));let d=[];return p.forEach(g=>d.push(g)),{mode:r.mode,value:s,pads:d}}else return r},P$=(e,r)=>{N_(e.inputs);let a=G_(e.inputs,r);e.compute(W_(e.inputs,a),{inputs:[0]})}}),Bo,Vd,Wd,Gd,Fd,F_,H_,Hd,jd,U$,L$,Kd,q$,V$,Zd,W$,G$,F$,H$,A1=Ee(()=>{bi(),ut(),ct(),ft(),Bo=e=>{if(Ft.webgpu.validateInputContent&&(!e||e.length!==1))throw new Error("Pool ops requires 1 input.")},Vd=(e,r,a)=>{let s=r.format==="NHWC",o=e.dims.slice();s&&o.splice(1,0,o.pop());let p=Object.hasOwnProperty.call(r,"dilations"),d=r.kernelShape.slice(),g=r.strides.slice(),m=p?r.dilations.slice():[],_=r.pads.slice();al.adjustPoolAttributes(a,o,d,g,m,_);let v=al.computePoolOutputShape(a,o,g,m,d,_,r.autoPad),x=Object.assign({},r);p?Object.assign(x,{kernelShape:d,strides:g,pads:_,dilations:m,cacheKey:r.cacheKey}):Object.assign(x,{kernelShape:d,strides:g,pads:_,cacheKey:r.cacheKey});let T=v.slice();return T.push(T.splice(1,1)[0]),[x,s?T:v]},Wd=(e,r)=>{let a=r.format==="NHWC",s=ge.size(e),o=ge.size(r.kernelShape),p=[{type:12,data:s},{type:12,data:o}],d=[{name:"outputSize",type:"u32"},{name:"kernelSize",type:"u32"}];if(r.kernelShape.length<=2){let g=r.kernelShape[r.kernelShape.length-1],m=r.strides[r.strides.length-1],_=r.pads[r.pads.length/2-1],v=r.pads[r.pads.length-1],x=!!(_+v);p.push({type:12,data:g},{type:12,data:m},{type:12,data:_},{type:12,data:v}),d.push({name:"kw",type:"u32"},{name:"sw",type:"u32"},{name:"pwStart",type:"u32"},{name:"pwEnd",type:"u32"});let T=!1;if(r.kernelShape.length===2){let C=r.kernelShape[r.kernelShape.length-2],A=r.strides[r.strides.length-2],R=r.pads[r.pads.length/2-2],H=r.pads[r.pads.length-2];T=!!(R+H),p.push({type:12,data:C},{type:12,data:A},{type:12,data:R},{type:12,data:H}),d.push({name:"kh",type:"u32"},{name:"sh",type:"u32"},{name:"phStart",type:"u32"},{name:"phEnd",type:"u32"})}return[p,d,!0,x,T]}else{if(a)throw new Error("Pooling with kernelShape.length > 2 is not supported for NHWC format.");let g=ge.computeStrides(r.kernelShape);p.push({type:12,data:g},{type:12,data:r.pads},{type:12,data:r.strides}),d.push({name:"kernelStrides",type:"u32",length:g.length},{name:"pads",type:"u32",length:r.pads.length},{name:"strides",type:"u32",length:r.strides.length});let m=r.pads.reduce((_,v)=>_+v);return[p,d,!!m,!1,!1]}},Gd=(e,r,a,s,o,p,d,g,m,_,v,x)=>{let T=o.format==="NHWC",C=r.type.value,A=je("output",r.type.tensor,s);if(o.kernelShape.length<=2){let R="",H="",U="",P=a-(T?2:1);if(v?R=`
                for (var i: u32 = 0u; i < uniforms.kw; i++) {
                  xIndices[${P}] = indices[${P}] * uniforms.sw - uniforms.pwStart + i;
                  if (xIndices[${P}] < 0 || xIndices[${P}]
                      >= uniforms.x_shape[${P}]) {
                    pad++;
                    continue;
                  }
                  let x_val = x[${r.indicesToOffset("xIndices")}];
                  ${p}
                }`:R=`
                for (var i: u32 = 0u; i < uniforms.kw; i++) {
                  xIndices[${P}] = indices[${P}] * uniforms.sw - uniforms.pwStart + i;
                  let x_val = x[${r.indicesToOffset("xIndices")}];
                  ${p}
                }`,o.kernelShape.length===2){let F=a-(T?3:2);x?H=`
                for (var j: u32 = 0u; j < uniforms.kh; j++) {
                  xIndices[${F}] = indices[${F}] * uniforms.sh - uniforms.phStart + j;
                  if (xIndices[${F}] < 0 || xIndices[${F}] >= uniforms.x_shape[${F}]) {
                    pad += i32(uniforms.kw);
                    continue;
                  }
              `:H=`
                for (var j: u32 = 0u; j < uniforms.kh; j++) {
                  xIndices[${F}] = indices[${F}] * uniforms.sh - uniforms.phStart + j;
                `,U=`
              }
            `}return`
            ${e.registerUniforms(m).declareVariables(r,A)}

            ${e.mainStart()}
              ${e.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}

              let indices = ${A.offsetToIndices("global_idx")};
              var xIndices = ${A.offsetToIndices("global_idx")};

              var value = ${C}(${g});
              var pad = 0;
              ${H}
              ${R}
              ${U}
              ${d}

              output[global_idx] = value;
            }`}else{if(T)throw new Error("Pooling with kernelShape.length > 2 is not supported for NHWC format.");let R=o.kernelShape.length,H=o.pads.length,U="";return _?U=`
                if (xIndices[j] >= uniforms.x_shape[j]) {
                  pad++;
                  isPad = true;
                  break;
                }
              }
              if (!isPad) {
                let x_val = x[${r.indicesToOffset("xIndices")}];
                ${p}
              }`:U=`
              }
              let x_val = x[${r.indicesToOffset("xIndices")}];
              ${p}
            `,`
            ${e.registerUniforms(m).declareVariables(r,A)}

            ${e.mainStart()}
              ${e.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
              let indices = ${A.offsetToIndices("global_idx")};
              var xIndices = ${A.offsetToIndices("global_idx")};

              var offsets: array<u32, ${R}>;

              var value = ${C}(${g});
              var pad = 0;
              var isPad = false;

              for (var i: u32 = 0u; i < uniforms.kernelSize; i++) {
                var offset = i;
                for (var j = 0u; j < ${R-1}u; j++) {
                  offsets[j] = offset / ${Qe("uniforms.kernelStrides","j",R)};
                  offset -= offsets[j] * ${Qe("uniforms.kernelStrides","j",R)};
                }
                offsets[${R-1}] = offset;

                isPad = false;
                for (var j = ${a-R}u; j < ${a}u; j++) {
                  xIndices[j] = indices[j] * ${Qe("uniforms.strides",`j - ${a-R}u`,R)}
                    + offsets[j - ${a-R}u] - ${Qe("uniforms.pads","j - 2u",H)};
                  ${U}
              }
              ${d}

              output[global_idx] = value;
            }`}},Fd=e=>`${e.format};${e.ceilMode};${e.autoPad};${e.kernelShape.length}`,F_=e=>`${Fd(e)};${e.countIncludePad}`,H_=e=>`${Fd(e)};${e.storageOrder};${e.dilations}`,Hd=e=>({format:e.format,autoPad:["NOTSET","VALID","SAME_UPPER","SAME_LOWER"][e.auto_pad],ceilMode:e.ceil_mode,kernelShape:e.kernel_shape,strides:e.strides,pads:e.pads}),jd=(e,r,a,s)=>{let[o,p]=Vd(r,s,a),d=$e("x",r.dataType,r.dims.length),g=d.type.value,m="value += x_val;",_="";o.countIncludePad?_+=`value /= ${g}(uniforms.kernelSize);`:_+=`value /= ${g}(i32(uniforms.kernelSize) - pad);`;let[v,x,T,C,A]=Wd(p,o);v.push(...Je(r.dims,p));let R=["rank"];return{name:e,shaderCache:{hint:`${s.cacheKey};${T};${C};${A}`,inputDependencies:R},getRunData:()=>({outputs:[{dims:p,dataType:r.dataType}],dispatchGroup:{x:Math.ceil(ge.size(p)/64)},programUniforms:v}),getShaderSource:H=>Gd(H,d,r.dims.length,p.length,o,m,_,0,x,T,C,A)}},U$=e=>{let r=e.count_include_pad!==0,a=Hd(e);if(a.ceilMode!==0)throw new Error("using ceil() in shape computation is not yet supported for AveragePool");let s={countIncludePad:r,...a,cacheKey:""};return{...s,cacheKey:F_(s)}},L$=(e,r)=>{Bo(e.inputs),e.compute(jd("AveragePool",e.inputs[0],!1,r))},Kd={autoPad:"",ceilMode:0,countIncludePad:!1,kernelShape:[],strides:[],pads:[],storageOrder:0,dilations:[]},q$=e=>{let r=e.format;return{format:r,...Kd,cacheKey:r}},V$=(e,r)=>{Bo(e.inputs),e.compute(jd("GlobalAveragePool",e.inputs[0],!0,r))},Zd=(e,r,a,s)=>{let[o,p]=Vd(r,s,a),d=`
      value = max(x_val, value);
    `,g="",m=$e("x",r.dataType,r.dims.length),_=["rank"],[v,x,T,C,A]=Wd(p,o);return v.push(...Je(r.dims,p)),{name:e,shaderCache:{hint:`${s.cacheKey};${T};${C};${A}`,inputDependencies:_},getRunData:()=>({outputs:[{dims:p,dataType:r.dataType}],dispatchGroup:{x:Math.ceil(ge.size(p)/64)},programUniforms:v}),getShaderSource:R=>Gd(R,m,r.dims.length,p.length,o,d,g,r.dataType===10?-65504:-1e5,x,T,C,A)}},W$=(e,r)=>{Bo(e.inputs),e.compute(Zd("MaxPool",e.inputs[0],!1,r))},G$=e=>{let r=e.storage_order,a=e.dilations,s=Hd(e);if(r!==0)throw new Error("column major storage order is not yet supported for MaxPool");if(s.ceilMode!==0)throw new Error("using ceil() in shape computation is not yet supported for MaxPool");let o={storageOrder:r,dilations:a,...s,cacheKey:""};return{...o,cacheKey:H_(o)}},F$=e=>{let r=e.format;return{format:r,...Kd,cacheKey:r}},H$=(e,r)=>{Bo(e.inputs),e.compute(Zd("GlobalMaxPool",e.inputs[0],!0,r))}}),j_,K_,j$,K$,O1=Ee(()=>{ut(),ct(),Jt(),ft(),j_=(e,r)=>{if(e.length<2||e.length>3)throw new Error("DequantizeLinear requires 2 or 3 inputs.");if(e.length===3&&e[1].dims===e[2].dims)throw new Error("x-scale and x-zero-point must have the same shape.");if(e.length===3&&e[0].dataType!==e[2].dataType)throw new Error("x and x-zero-point must have the same data type.");if(e[1].dims.length!==0&&e[1].dims.length!==1&&e[1].dims.length!==e[0].dims.length)throw new Error("scale input must be a scalar, a 1D tensor, or have the same rank as the input tensor.");if(e.length>2){if(e[0].dataType!==e[2].dataType)throw new Error("x and x-zero-point must have the same data type.");if(e[1].dims.length!==e[2].dims.length)throw new Error("scale and zero-point inputs must have the same rank.");if(!e[1].dims.map((a,s)=>a===e[2].dims[s]).reduce((a,s)=>a&&s,!0))throw new Error("scale and zero-point inputs must have the same shape.")}if(r.blockSize>0){if(e[1].dims.length===0||e[1].dims.length===1&&e[1].dims[0]===1)throw new Error("blockSize must be set only for block quantization.");if(!e[1].dims.map((o,p)=>p===r.axis||o===e[0].dims[p]).reduce((o,p)=>o&&p,!0))throw new Error("For block qunatization, scale input shape to match the input shape except for the axis");if(e[1].dims.length!==e[0].dims.length)throw new Error("For block qunatization the scale input rank must be the same as the x rank.");let a=e[0].dims[r.axis],s=e[1].dims[r.axis];if(r.blockSize<Math.ceil(a/s)||r.blockSize>Math.ceil(a/(s-1)-1))throw new Error("blockSize must be with in the range [ceil(dI / Si), ceil(dI / (Si - 1) - 1)].")}},K_=(e,r)=>{let a=ge.normalizeAxis(r.axis,e[0].dims.length),s=e[0].dataType,o=s===3,p=e[0].dims,d=e[1].dataType,g=ge.size(p),m=s===3||s===2,_=m?[Math.ceil(ge.size(e[0].dims)/4)]:e[0].dims,v=e[1].dims,x=e.length>2?e[2]:void 0,T=x?m?[Math.ceil(ge.size(x.dims)/4)]:x.dims:void 0,C=v.length===0||v.length===1&&v[0]===1,A=C===!1&&v.length===1,R=Yt(g),H=C&&(!m||R===4),U=H?R:1,P=H&&!m?R:1,F=$e("input",m?12:s,_.length,P),G=$e("scale",d,v.length),K=x?$e("zero_point",m?12:s,T.length):void 0,ee=je("output",d,p.length,U),ae=[F,G];K&&ae.push(K);let B=[_,v];x&&B.push(T);let me=[{type:12,data:g/U},{type:12,data:a},{type:12,data:r.blockSize},...Je(...B,p)],_e=Re=>{let Ue=[{name:"output_size",type:"u32"},{name:"axis",type:"u32"},{name:"block_size",type:"u32"}];return`
      ${Re.registerUniforms(Ue).declareVariables(...ae,ee)}
      ${Re.mainStart()}
          ${Re.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
          let output_indices = ${ee.offsetToIndices("global_idx")};

          // Set input x
          ${m?`
            let input = ${F.getByOffset("global_idx / 4")};
            let x_vec = ${o?"unpack4xI8(input)":"unpack4xU8(input)"};
            let x_value = ${U===1?"x_vec[global_idx % 4]":"x_vec"};`:`let x_value = ${F.getByOffset("global_idx")};`};

          // Set scale input
          ${C?`let scale_value= ${G.getByOffset("0")}`:A?`
            let scale_index = ${ee.indicesGet("output_indices","uniforms.axis")};
            let scale_value= ${G.getByOffset("scale_index")};`:`
            var scale_indices: ${G.type.indices} = output_indices;
            let index = ${G.indicesGet("scale_indices","uniforms.axis")} / uniforms.block_size;
            ${G.indicesSet("scale_indices","uniforms.axis","index")};
            let scale_value= ${G.getByIndices("scale_indices")};`};

          // Set zero-point input
          ${K?C?m?`
                let zero_point_input = ${K.getByOffset("0")};
                let zero_point_vec =  ${o?"unpack4xI8(zero_point_input)":"unpack4xU8(zero_point_input)"};
                let zero_point_value= zero_point_vec[0]`:`let zero_point_value = ${K.getByOffset("0")}`:A?m?`
                let zero_point_index = ${ee.indicesGet("output_indices","uniforms.axis")};
                let zero_point_input = ${K.getByOffset("zero_point_index / 4")};
                let zero_point_vec =  ${o?"unpack4xI8(zero_point_input)":"unpack4xU8(zero_point_input)"};
                let zero_point_value = zero_point_vec[zero_point_index % 4]`:`
                let zero_point_index = ${ee.indicesGet("output_indices","uniforms.axis")};
                let zero_point_value = ${K.getByOffset("zero_point_index")};`:m?`
                let zero_point_offset = ${G.indicesToOffset("scale_indices")};
                let zero_point_input = ${K.getByOffset("zero_point_offset / 4")};
                let zero_point_vec = ${o?"unpack4xI8(zero_point_input)":"unpack4xU8(zero_point_input)"};
                let zero_point_value = zero_point_vec[zero_point_offset % 4];`:`let zero_point_value = ${K.getByIndices("scale_indices")};`:`let zero_point_value = ${m?o?"i32":"u32":F.type.value}(0);`};
      // Compute and write output
      ${ee.setByOffset("global_idx",`${ee.type.value}(x_value - zero_point_value) * scale_value`)};
      }`};return{name:"DequantizeLinear",shaderCache:{hint:r.cacheKey,inputDependencies:K?["rank","rank","rank"]:["rank","rank"]},getShaderSource:_e,getRunData:()=>({outputs:[{dims:p,dataType:d}],dispatchGroup:{x:Math.ceil(g/U/64),y:1,z:1},programUniforms:me})}},j$=(e,r)=>{j_(e.inputs,r),e.compute(K_(e.inputs,r))},K$=e=>Nt({axis:e.axis,blockSize:e.blockSize})}),Z_,Q_,Z$,R1=Ee(()=>{bi(),ut(),ft(),Z_=(e,r,a)=>{let s=e===r,o=e<r&&a<0,p=e>r&&a>0;if(s||o||p)throw new Error("Range these inputs' contents are invalid.")},Q_=(e,r,a,s)=>{let o=Math.abs(Math.ceil((r-e)/a)),p=[o],d=o,g=[{type:12,data:d},{type:s,data:e},{type:s,data:a},...Je(p)],m=_=>{let v=je("output",s,p.length),x=v.type.value,T=[{name:"outputSize",type:"u32"},{name:"start",type:x},{name:"delta",type:x}];return`
        ${_.registerUniforms(T).declareVariables(v)}
        ${_.mainStart()}
        ${_.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
        output[global_idx] = uniforms.start + ${x}(global_idx) * uniforms.delta;
      }`};return{name:"Range",shaderCache:{hint:`${s}`},getShaderSource:m,getRunData:()=>({outputs:[{dims:p,dataType:s}],dispatchGroup:{x:Math.ceil(d/64)},programUniforms:g})}},Z$=e=>{let r=0,a=0,s=0;e.inputs[0].dataType===6?(r=e.inputs[0].getInt32Array()[0],a=e.inputs[1].getInt32Array()[0],s=e.inputs[2].getInt32Array()[0]):e.inputs[0].dataType===1&&(r=e.inputs[0].getFloat32Array()[0],a=e.inputs[1].getFloat32Array()[0],s=e.inputs[2].getFloat32Array()[0]),Ft.webgpu.validateInputContent&&Z_(r,a,s),e.compute(Q_(r,a,s,e.inputs[0].dataType),{inputs:[]})}}),X_,Y_,Q$,X$,B1=Ee(()=>{ut(),ct(),Jt(),ft(),X_=(e,r,a,s)=>{if(e!=="none"&&s!=="i32"&&s!=="u32"&&s!=="f32")throw new Error(`Input ${s} is not supported with reduction ${e}.`);let o=`{
                var oldValue = 0;
                loop {
                  let newValueF32 =`,p=`;
                  let newValue = bitcast<i32>(newValueF32);
                  let res = atomicCompareExchangeWeak(&${r}, oldValue, newValue);
                  if res.exchanged {
                    break;
                  }
                  oldValue = res.old_value;
                }
              }`;switch(e){case"none":return`${r}=${a};`;case"add":return s==="i32"||s==="u32"?`atomicAdd(&${r}, bitcast<${s}>(${a}));`:`
              ${o}bitcast<${s}>(oldValue) + (${a})${p}`;case"max":return s==="i32"||s==="u32"?`atomicMax(&${r}, bitcast<${s}>(${a}));`:`
                ${o}max(bitcast<f32>(oldValue), (${a}))${p}`;case"min":return s==="i32"||s==="u32"?`atomicMin(&${r}, bitcast<${s}>(${a}));`:`${o}min(bitcast<${s}>(oldValue), (${a}))${p}`;case"mul":return`${o}(bitcast<${s}>(oldValue) * (${a}))${p}`;default:throw new Error(`Reduction ${e} is not supported.`)}},Y_=(e,r)=>{let a=e[0].dims,s=e[1].dims,o=a,p=1,d=Math.ceil(ge.sizeToDimension(s,s.length-1)/p),g=s[s.length-1],m=ge.sizeFromDimension(a,g),_=[{type:12,data:d},{type:12,data:g},{type:12,data:m},...Je(e[1].dims,e[2].dims,o)],v=x=>{let T=$e("indices",e[1].dataType,e[1].dims.length),C=$e("updates",e[2].dataType,e[2].dims.length,p),A=r.reduction!=="none"&&r.reduction!==""?T0("output",e[0].dataType,o.length):je("output",e[0].dataType,o.length,p);return`
      ${x.registerUniform("output_size","u32").registerUniform("last_index_dimension","u32").registerUniform("num_updates_elements","u32").declareVariables(T,C,A)}
      ${x.mainStart()}
        ${x.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
  var data_offset = 0u;
  let indices_start = uniforms.last_index_dimension * global_idx;
  let indices_end = indices_start + uniforms.last_index_dimension;
  for (var i = indices_start; i < indices_end; i++) {
    var index = i32(indices[i].x);
    ${e[0].dims.length===1?`
    let element_count_dim = uniforms.output_strides;
    let dim_value = uniforms.output_shape;`:`
    let element_count_dim = uniforms.output_strides[i - indices_start];
    let dim_value = uniforms.output_shape[i - indices_start];`}
    if (index >= 0) {
      if (index >= i32(dim_value)) {
        index = i32(dim_value - 1);
      }
    } else {
      if (index < -i32(dim_value)) {
        index = 0;
      } else {
        index += i32(dim_value);
      }
    }
    data_offset += u32((u32(index) * element_count_dim));
  }

  for (var i = 0u; i < uniforms.num_updates_elements; i++) {
    let value = updates[uniforms.num_updates_elements * global_idx + i];
    ${X_(r.reduction,"output[data_offset + i]","value",A.type.value)}
  }

      }`};return{name:"ScatterND",shaderCache:{hint:`${r.cacheKey}_${r.reduction}`,inputDependencies:["rank","rank"]},getRunData:()=>({outputs:[{dims:o,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(d/64)},programUniforms:_}),getShaderSource:v}},Q$=e=>Nt({reduction:e.reduction}),X$=(e,r)=>{e.compute(Y_(e.inputs,r),{inputs:[e.inputs[1],e.inputs[2]],outputs:[]})}}),J_,ew,tw,Qd,rw,iw,aw,nw,sw,ow,uw,lw,Xd,dw,pw,cw,hw,fw,Y$,J$,M1=Ee(()=>{ut(),ct(),Jt(),ft(),J_=(e,r)=>{if(e.every(a=>a>0||(()=>{throw new Error("Resize requires scales input values to be positive")})),e.length>0){if(r.mode==="linear"){if(!(e.length===2||e.length===3||e.length===4&&e[0]===1&&e[1]===1||e.length===4&&e[0]===1&&e[3]===1||e.length===5&&e[0]===1&&e[1]===1))throw new Error(`For linear mode, Resize requires scales to be 2D, 3D, 4D with either two outermost or one innermost and
            one outermost scale values equal to 1, or 5D with two outermost scale values equal to 1`)}else if(r.mode==="cubic"&&!(e.length===2||e.length===4&&e[0]===1&&e[1]===1||e.length===4&&e[0]===1&&e[3]===1))throw new Error("Resize requires scales input size to be 2 or 4 for cubic mode")}},ew=(e,r,a)=>{r.every(o=>o>=0&&o<a||(()=>{throw new Error("Resize requires axes input values to be positive and less than rank")}));let s=new Array(a).fill(1);return r.forEach((o,p)=>s[o]=e[p]),s},tw=(e,r,a,s,o,p)=>{let[d,g,m]=a>10?[1,2,3]:[-1,e.length>1?1:-1,-1],_=e[0].dims.length;if(d>0&&e.length>d&&e[d].dims.length>0)e[d].getFloat32Array().forEach(v=>p.push(v));else if(r.coordinateTransformMode==="tf_crop_and_resize")throw new Error("Resize requires RoI input to be specified when coordinateTransformMode is tfCropAndResize");if(g>0&&e.length>g&&e[g].dims.length===1&&e[g].dims[0]>0){if(e[g].getFloat32Array().forEach(v=>s.push(v)),s.length!==0&&s.length!==_&&a>=18&&s.length!==r.axes.length)throw new Error("Resize requires scales input size to be same as input rank or axes size for opset 18 and up");J_(s,r),r.axes.length>0&&ew(s,r.axes,_).forEach((v,x)=>s[x]=v)}if(m>0&&e.length>m&&e[m].dims.length===1&&e[m].dims[0]>0&&(e[m].getBigInt64Array().forEach(v=>o.push(Number(v))),o.length!==0&&o.length!==_&&a>=18&&o.length!==r.axes.length))throw new Error("Resize requires sizes input size to be same as input rank or axes size for opset 18 and up");if(r.axes.length>0){if(s.length!==0&&s.length!==r.axes.length)throw new Error('Resize requires "scales" input size to be of axes rank when axes attributes is specified');if(o.length!==0&&o.length!==r.axes.length)throw new Error('Resize requires "sizes" input size to be of rank axes rank when axes attributes is specified')}if(typeof s<"u"&&typeof o<"u"&&s.length>0&&o.length>_)throw new Error("Resize requires only of scales or sizes to be specified")},Qd=(e,r,a,s)=>`
  // The whole part and the fractional part are calculated separately due to inaccuracy of floating
  // point division. As an example, f32(21) / f32(7) may evaluate to 2.99... instead of 3, causing an
  // offset-by-one error later in floor().
  let big = (${e}) * (${r});
  let whole = ${s}(big / (${a}));
  let fract = ${s}(big % (${a})) / ${s}(${a});
  return whole + fract;
`,rw=(e,r)=>`fn getOriginalCoordinateFromResizedCoordinate(xResized: u32, xScale: f32, lengthResized: u32,
     lengthOriginal: u32, roiStart: f32, roiEnd: f32) -> ${r} { `+(()=>{switch(e){case"asymmetric":return`
          if (xScale < 1.0 || floor(xScale) != xScale) {
            return ${r}(xResized) / ${r}(xScale);
          } else {
            ${Qd("xResized","lengthOriginal","lengthResized",r)}
          }
        `;case"pytorch_half_pixel":return`if (lengthResized > 1) {
                    return (${r}(xResized) + 0.5) / ${r}(xScale) - 0.5;
                  } else {
                    return 0.0;
                  }`;case"tf_half_pixel_for_nn":return`return (${r}(xResized) + 0.5) / ${r}(xScale);`;case"align_corners":return`if (lengthResized == 1) {
                    return 0.0;
                  } else {
                    ${Qd("xResized","lengthOriginal - 1","lengthResized - 1",r)}
                  }`;case"tf_crop_and_resize":return`if (lengthResized > 1) {
                    return ${r}(roiStart) * ${r}(lengthOriginal - 1) +
                        (${r}(xResized) * ${r}(roiEnd - roiStart) * ${r}(lengthOriginal - 1)) /
                        ${r}(lengthResized - 1);
                  } else {
                    return 0.5 * ${r}(roiStart + roiEnd) * ${r}(lengthOriginal - 1);
                  }`;case"half_pixel_symmetric":return`const outputWidth = ${r}xScale * ${r}(lengthResized);
                  const adjustment = ${r}(lengthResized) / outputWidth;
                  const center = ${r}(lengthOriginal) / 2;
                  const offset = center * (1 - adjustment);
                  return offset + ((${r}(xResized) + 0.5) / ${r}(xScale)) - 0.5;`;case"half_pixel":return`return ((${r}(xResized) + 0.5) / ${r}(xScale)) - 0.5;`;default:throw new Error(`Coordinate transform mode ${e} is not supported`)}})()+"}",iw=(e,r,a)=>`fn getNearestPixelFromOriginal(xOriginal: ${a}, isDownSample: bool) -> ${a} {`+(()=>{switch(e){case"round_prefer_ceil":return"if (fract(xOriginal) == 0.5) {             return ceil(xOriginal);           } else {             return round(xOriginal);           }";case"floor":return"return floor(xOriginal);";case"ceil":return"return ceil(xOriginal);";case"round_prefer_floor":return"if (fract(xOriginal) == 0.5) {                     return floor(xOriginal);                   } else {                     return round(xOriginal);                   }";case"simple":default:if(r<11)return"if (isDownSample)                     {                       return ceil(xOriginal);                     } else {                       return xOriginal;                     }";throw new Error(`Nearest mode ${e} is not supported`)}})()+"}",aw=(e,r,a)=>{let s=new Array(a).fill(0).concat(new Array(a).fill(1)),o=e.length===0?s:e.slice();return r.length>0?(r.forEach((p,d)=>{s[p]=o[d],s[d+a]=o[r.length+d]}),s):o},nw=(e,r,a,s)=>{let o=[];if(a.length>0)if(s.length>0){if(e.forEach(p=>o.push(p)),Math.max(...s)>e.length)throw new Error("axes is out of bound");s.forEach((p,d)=>o[p]=a[d])}else a.forEach(p=>o.push(p));else{if(r.length===0)throw new Error("Resize requires either scales or sizes.");o=e.map((p,d)=>Math.round(p*r[d]))}return o},sw=(e,r,a)=>{let s=(()=>{switch(a.keepAspectRatioPolicy){case"not_larger":return a.axes.length>0?Math.min(...a.axes.map(p=>r[p]),Number.MAX_VALUE):Math.min(...r,Number.MAX_VALUE);case"not_smaller":return a.axes.length>0?Math.max(...a.axes.map(p=>r[p]),Number.MIN_VALUE):Math.max(...r,Number.MIN_VALUE);default:throw new Error(`Keep aspect ratio policy ${a.keepAspectRatioPolicy} is not supported`)}})();r.fill(1,0,r.length);let o=e.slice();return a.axes.length>0?(a.axes.forEach(p=>r[p]=s),a.axes.forEach(p=>o[p]=Math.round(e[p]*r[p]))):(r.fill(s,0,r.length),o.forEach((p,d)=>o[d]=Math.round(p*r[d]))),o},ow=(e,r,a,s,o)=>`
    fn calculateOriginalIndicesFromOutputIndices(output_indices: ${e.type.indices}) -> array<${e.type.value}, ${a.length}> {
      var original_indices: array<${e.type.value}, ${a.length}>;
      for (var i:u32 = 0; i < ${a.length}; i++) {
        var output_index = ${e.indicesGet("output_indices","i")};
        var scale = ${Qe("uniforms.scales","i",s)};
        var roi_low = ${Qe("uniforms.roi","i",o)};
        var roi_hi = ${Qe("uniforms.roi",`i + ${r.length}`,o)};
        if (scale == 1.0) {
          original_indices[i] = ${e.type.value}(output_index);
        } else {
          var input_shape_i = ${Qe("uniforms.input_shape","i",r.length)};
          var output_shape_i = ${Qe("uniforms.output_shape","i",a.length)};
          original_indices[i] = getOriginalCoordinateFromResizedCoordinate(output_index, scale, output_shape_i,
                                                                           input_shape_i, roi_low, roi_hi);
        }
      }
      return original_indices;
    }`,uw=(e,r,a,s,o,p,d)=>`
    fn calculateInputIndicesFromOutputIndices(output_indices: ${r.type.indices}) -> ${e.type.indices} {
      var input_indices: ${e.type.indices};
      for (var i:u32 = 0; i < ${s.length}; i++) {
        var output_index = ${r.indicesGet("output_indices","i")};
        var input_index: u32;
        var scale = ${Qe("uniforms.scales","i",o)};
        if (scale == 1.0) {
          input_index = output_index;
        } else {
          var roi_low = ${Qe("uniforms.roi","i",p)};
          var roi_hi = ${Qe("uniforms.roi",`i + ${a.length}`,p)};
          var input_shape_i = ${Qe("uniforms.input_shape","i",a.length)};
          var output_shape_i = ${Qe("uniforms.output_shape","i",s.length)};
          var original_idx = getOriginalCoordinateFromResizedCoordinate(output_index, scale, output_shape_i,
                                                                        input_shape_i, roi_low, roi_hi);
          if (!${d} || (original_idx >= 0 && original_idx < ${r.type.value}(input_shape_i))) {
            if (original_idx < 0) {
              input_index = 0;
            } else if (original_idx > ${r.type.value}(input_shape_i - 1)) {
              input_index = input_shape_i - 1;
            } else {
              input_index = u32(getNearestPixelFromOriginal(original_idx, scale < 1));
            }
          } else {
            input_index = u32(original_idx);
          }
        }
        ${e.indicesSet("input_indices","i","input_index")}
      }
      return input_indices;
    }`,lw=(e,r)=>`
    fn checkInputIndices(input_indices: ${e.type.indices}) -> bool {
      for (var i:u32 = 0; i < ${r.length}; i++) {
        var input_index = ${e.indicesGet("input_indices","i")};
        if (input_index < 0 || input_index >= ${Qe("uniforms.input_shape","i",r.length)}) {
          return false;
        }
      }
      return true;
    }`,Xd=(e,r,a,s)=>e.rank>s?`
    ${e.indicesSet("input_indices",r,"channel")};
    ${e.indicesSet("input_indices",a,"batch")};
`:"",dw=(e,r,a,s,o)=>{let[p,d,g,m]=a.length===2?[-1,0,1,-1]:[0,2,3,1],_=e.type.value;return`
    fn getInputValue(batch: u32, channel: u32, row: u32, col: u32) -> ${_} {
      var input_indices: ${e.type.indices};
      ${e.indicesSet("input_indices",d,`max(0, min(row, ${a[d]} - 1))`)};
      ${e.indicesSet("input_indices",g,`max(0, min(col, ${a[g]} - 1))`)};
      ${Xd(e,m,p,2)}
      return ${e.getByIndices("input_indices")};
    }

    fn bilinearInterpolation(output_indices: ${r.type.indices}) -> ${_} {
      var originalIndices = calculateOriginalIndicesFromOutputIndices(output_indices);
      var row:${_} = originalIndices[${d}];
      var col:${_} = originalIndices[${g}];
      ${s?`if (row < 0 || row > (${a[d]} - 1) || col < 0 || col > (${a[g]} - 1)) {
        return ${o};
      }`:""};
      row = max(0, min(row, ${a[d]} - 1));
      col = max(0, min(col, ${a[g]} - 1));
      var row1: u32 = u32(row);
      var col1: u32 = u32(col);
      var row2: u32 = u32(row + 1);
      var col2: u32 = u32(col + 1);
      var channel: u32 = ${a.length>2?`u32(originalIndices[${m}])`:"0"};
      var batch: u32 =  ${a.length>2?`u32(originalIndices[${p}])`:"0"};
      var x11: ${_} = getInputValue(batch, channel, row1, col1);
      var x12: ${_} = getInputValue(batch, channel, row1, col2);
      var x21: ${_} = getInputValue(batch, channel, row2, col1);
      var x22: ${_} = getInputValue(batch, channel, row2, col2);
      var dx1: ${_} = abs(row - ${_}(row1));
      var dx2: ${_} = abs(${_}(row2) - row);
      var dy1: ${_} = abs(col - ${_}(col1));
      var dy2: ${_} = abs(${_}(col2) - col);
      if (row1 == row2) {
        dx1 = 0.5;
        dx2 = 0.5;
      }
      if (col1 == col2) {
        dy1 = 0.5;
        dy2 = 0.5;
      }
      return (x11 * dx2 * dy2 + x12 * dx2 * dy1 + x21 * dx1 * dy2 + x22 * dx1 * dy1);
    }`},pw=(e,r,a,s,o,p,d,g,m,_)=>{let v=a.length===2,[x,T]=v?[0,1]:[2,3],C=e.type.value,A=R=>{let H=R===x?"row":"col";return`
      fn ${H}CubicInterpolation(input_indices: ${e.type.indices}, output_indices: ${r.type.indices}) -> ${C} {
        var output_index = ${r.indicesGet("output_indices",R)};
        var originalIdx: ${C} = getOriginalCoordinateFromResizedCoordinate(output_index, ${o[R]},
        ${s[R]}, ${a[R]}, ${p[R]}, ${p[R]} + ${a.length});
        var fractOriginalIdx: ${C} = originalIdx - floor(originalIdx);
        var coefs = getCubicInterpolationCoefs(fractOriginalIdx);

        if (${g} && (originalIdx < 0 || originalIdx > (${a[R]} - 1))) {
          return ${m};
        }
        var data: array<${C}, 4> = array<${C}, 4>(0.0, 0.0, 0.0, 0.0);
        for (var i: i32 = -1; i < 3; i++) {
          var ${H}: ${C} = originalIdx + ${C}(i);
          if (${H} < 0 || ${H} >= ${a[R]}) {
            ${_?`coefs[i + 1] = 0.0;
                        continue;`:g?`return ${m};`:`${H} = max(0, min(${H}, ${a[R]} - 1));`};
          }
        var input_indices_copy: ${e.type.indices} = input_indices;
          ${e.indicesSet("input_indices_copy",R,`u32(${H})`)};
          data[i + 1] = ${R===x?e.getByIndices("input_indices_copy"):"rowCubicInterpolation(input_indices_copy, output_indices)"};
        }
        return cubicInterpolation1D(data, coefs);
      }`};return`
    ${A(x)};
    ${A(T)};
  fn getCubicInterpolationCoefs(s: ${C}) -> array<${C}, 4> {
    var absS = abs(s);
    var coeffs: array<${C}, 4> = array<${C}, 4>(0.0, 0.0, 0.0, 0.0);
    var oneMinusAbsS: ${C} = 1.0 - absS;
    var twoMinusAbsS: ${C} = 2.0 - absS;
    var onePlusAbsS: ${C} = 1.0 + absS;
    coeffs[0] = ((${d} * onePlusAbsS - 5 * ${d}) * onePlusAbsS + 8 * ${d}) * onePlusAbsS - 4 * ${d};
    coeffs[1] = ((${d} + 2) * absS - (${d} + 3)) * absS * absS + 1;
    coeffs[2] = ((${d} + 2) * oneMinusAbsS - (${d} + 3)) * oneMinusAbsS * oneMinusAbsS + 1;
    coeffs[3] = ((${d} * twoMinusAbsS - 5 * ${d}) * twoMinusAbsS + 8 * ${d}) * twoMinusAbsS - 4 * ${d};
    return coeffs;
  }

  fn cubicInterpolation1D(x: array<${C}, 4>, coefs: array<${C}, 4>) -> ${C} {
    var coefsSum: ${C} = coefs[0] + coefs[1] + coefs[2] + coefs[3];
    return (x[0] * coefs[0] + x[1] * coefs[1]+ x[2] * coefs[2]+ x[3] * coefs[3]) / coefsSum;
  }

  fn bicubicInterpolation(output_indices: ${r.type.indices}) -> ${C} {
    var input_indices: ${e.type.indices} = output_indices;
    return colCubicInterpolation(input_indices, output_indices);
  }
    `},cw=(e,r,a,s,o)=>{let[p,d,g,m,_]=a.length===3?[-1,0,1,2,-1]:[0,2,3,4,1],v=e.type.value;return`
    fn getInputValue(batch: u32, channel: u32, depth:u32, height: u32, width: u32) -> ${v} {
      var input_indices: ${e.type.indices};
      ${e.indicesSet("input_indices",d,`max(0, min(depth, ${a[d]} - 1))`)};
      ${e.indicesSet("input_indices",g,`max(0, min(height, ${a[g]} - 1))`)};
      ${e.indicesSet("input_indices",m,`max(0, min(width, ${a[m]} - 1))`)};
      ${Xd(e,_,p,3)}
      return ${e.getByIndices("input_indices")};
    }

    fn trilinearInterpolation(output_indices: ${r.type.indices}) -> ${v} {
      var originalIndices = calculateOriginalIndicesFromOutputIndices(output_indices);
      var depth:${v} = originalIndices[${d}];
      var height:${v} = originalIndices[${g}];
      var width:${v} = originalIndices[${m}];
      ${s?`if (depth < 0 || depth > (${a[d]} - 1) || height < 0 || height > (${a[g]} - 1) || width < 0 || (width > ${a[m]} - 1)) {
      return ${o};
        }`:""};

    depth = max(0, min(depth, ${a[d]} - 1));
      height = max(0, min(height, ${a[g]} - 1));
      width = max(0, min(width, ${a[m]} - 1));
      var depth1: u32 = u32(depth);
      var height1: u32 = u32(height);
      var width1: u32 = u32(width);
      var depth2: u32 = u32(depth + 1);
      var height2: u32 = u32(height + 1);
      var width2: u32 = u32(width + 1);
      var channel: u32 = ${a.length>3?`u32(originalIndices[${_}])`:"0"};
      var batch: u32 =  ${a.length>3?`u32(originalIndices[${p}])`:"0"};

      var x111: ${v} = getInputValue(batch, channel, depth1, height1, width1);
      var x112: ${v} = getInputValue(batch, channel, depth1, height1, width2);
      var x121: ${v} = getInputValue(batch, channel, depth1, height2, width1);
      var x122: ${v} = getInputValue(batch, channel, depth1, height2, width2);
      var x211: ${v} = getInputValue(batch, channel, depth2, height1, width1);
      var x212: ${v} = getInputValue(batch, channel, depth2, height1, width2);
      var x221: ${v} = getInputValue(batch, channel, depth2, height2, width1);
      var x222: ${v} = getInputValue(batch, channel, depth2, height2, width2);
      var dx1: ${v} = abs(depth - ${v}(depth1));
      var dx2: ${v} = abs(${v}(depth2) - depth);
      var dy1: ${v} = abs(height - ${v}(height1));
      var dy2: ${v} = abs(${v}(height2) - height);
      var dz1: ${v} = abs(width - ${v}(width1));
      var dz2: ${v} = abs(${v}(width2) - width);
      if (depth1 == depth2) {
        dx1 = 0.5;
        dx2 = 0.5;
      }
      if (height1 == height2) {
        dy1 = 0.5;
        dy2 = 0.5;
      }
      if (width1 == width2) {
        dz1 = 0.5;
        dz2 = 0.5;
      }
      return (x111 * dx2 * dy2 * dz2 + x112 * dx2 * dy2 * dz1 + x121 * dx2 * dy1 *dz2 + x122 * dx2 * dy1 * dz1 +
              x211 * dx1 * dy2 * dz2 + x212 * dx1 * dy2 * dz1 + x221 * dx1 * dy1 *dz2 + x222 * dx1 * dy1 * dz1);
    }`},hw=(e,r,a,s,o,p)=>{let d=e.dims,g=aw(p,r.axes,d.length),m=nw(d,s,o,r.axes),_=s.slice();s.length===0&&(_=d.map((P,F)=>P===0?1:m[F]/P),r.keepAspectRatioPolicy!=="stretch"&&(m=sw(d,_,r)));let v=je("output",e.dataType,m.length),x=$e("input",e.dataType,d.length),T=ge.size(m),C=d.length===m.length&&d.every((P,F)=>P===m[F]),A=r.coordinateTransformMode==="tf_crop_and_resize",R=r.extrapolationValue,H=x.type.value,U=P=>`
      ${C?"":`
      ${rw(r.coordinateTransformMode,H)};
      ${(()=>{switch(r.mode){case"nearest":return`
              ${lw(x,d)};
              ${iw(r.nearestMode,a,H)};
              ${uw(x,v,d,m,_.length,g.length,A)};
              `;case"linear":return`
              ${ow(v,d,m,_.length,g.length)};
              ${(()=>{if(d.length===2||d.length===4)return`${dw(x,v,d,A,R)}`;if(d.length===3||d.length===5)return`${cw(x,v,d,A,R)}`;throw Error("Linear mode only supports input dims 2, 3, 4 and 5 are supported in linear mode.")})()};
            `;case"cubic":return`
            ${(()=>{if(d.length===2||d.length===4)return`${pw(x,v,d,m,_,g,r.cubicCoeffA,A,r.extrapolationValue,r.excludeOutside)}`;throw Error("Cubic mode only supports input dims 2 and 4 are supported in linear mode.")})()};
            `;default:throw Error("Invalid resize mode")}})()};
      `}
      ${P.registerUniform("output_size","u32").registerUniform("scales","f32",_.length).registerUniform("roi","f32",g.length).declareVariables(x,v)}
      ${P.mainStart()}
        ${P.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
        ${C?"output[global_idx] = input[global_idx];":`
        let output_indices = ${v.offsetToIndices("global_idx")};
        var input_indices: ${x.type.indices};
        ${(()=>{switch(r.mode){case"nearest":return`input_indices = calculateInputIndicesFromOutputIndices(output_indices);
                if (checkInputIndices(input_indices)) {
                  output[global_idx] = ${x.getByIndices("input_indices")};
                } else {
                  output[global_idx] = ${r.extrapolationValue};
                }`;case"linear":return`output[global_idx] = ${d.length===2||d.length===4?"bilinearInterpolation":"trilinearInterpolation"}(output_indices);`;case"cubic":return"output[global_idx] = bicubicInterpolation(output_indices);";default:throw Error(`Unsupported resize mode: ${r.mode}`)}})()};
`}
      }`;return{name:"Resize",shaderCache:{hint:`${r.cacheKey}|${a}|${_.length>0?r.mode==="cubic"?_:_.length:""}|${o.length>0?o:""}|${g.length>0?g:""}|${C}|${r.mode==="nearest"?d.length:d}`,inputDependencies:["rank"]},getShaderSource:U,getRunData:()=>({outputs:[{dims:m,dataType:e.dataType}],dispatchGroup:{x:Math.ceil(T/64)},programUniforms:[{type:12,data:T},{type:1,data:_},{type:1,data:g},...Je(d,m)]})}},fw=e=>{let r=e.customDataBuffer;return new Uint32Array(r,r.byteOffset,1)[0]},Y$=(e,r)=>{let a=[],s=[],o=[],p=fw(e);if(r.antialias!==0)throw Error("Only default value (0) for Antialias attribute is supported");tw(e.inputs,r,p,a,s,o),e.compute(hw(e.inputs[0],r,p,a,s,o),{inputs:[0]})},J$=e=>{let r=e.antialias,a=e.axes,s=e.coordinateTransformMode,o=e.cubicCoeffA,p=e.excludeOutside!==0,d=e.extrapolationValue,g=e.keepAspectRatioPolicy,m=e.mode,_=e.nearestMode===""?"simple":e.nearestMode;return Nt({antialias:r,axes:a,coordinateTransformMode:s,cubicCoeffA:o,excludeOutside:p,extrapolationValue:d,keepAspectRatioPolicy:g,mode:m,nearestMode:_})}}),mw,gw,ev,D1=Ee(()=>{ut(),ct(),ft(),mw=e=>{if(!e||e.length<3)throw new Error("layerNorm requires at least 3 inputs.");let r=e[0],a=e[1],s=e[2];if(r.dataType!==a.dataType||r.dataType!==s.dataType)throw new Error("All inputs must have the same data type");if(r.dims.length!==3&&r.dims.length!==2)throw new Error("Input must be 2D or 3D");if(a.dims.length!==3&&a.dims.length!==2)throw new Error("Skip must be 2D or 3D");let o=r.dims[r.dims.length-1],p=r.dims[r.dims.length-2];if(a.dims[a.dims.length-1]!==o)throw new Error("Skip must have the same hidden size as input");if(a.dims[a.dims.length-2]!==p)throw new Error("Skip must have the same sequence length as input");if(s.dims.length!==1)throw new Error("Gamma must be 1D");if(s.dims[s.dims.length-1]!==o)throw new Error("Gamma must have the same hidden size as input");if(e.length>3){let d=e[3];if(d.dims.length!==1)throw new Error("Beta must be 1D");if(d.dims[d.dims.length-1]!==o)throw new Error("Beta must have the same hidden size as input")}if(e.length>4){let d=e[4];if(d.dims.length!==1)throw new Error("Bias must be 1D");if(d.dims[d.dims.length-1]!==o)throw new Error("Bias must have the same hidden size as input")}},gw=(e,r,a,s)=>{let o=r.simplified,p=e[0].dims,d=ge.size(p),g=p,m=d,_=p.slice(-1)[0],v=s?p.slice(0,-1).concat(1):[],x=!o&&e.length>3,T=e.length>4,C=s&&a>1,A=s&&a>2,R=a>3,H=64,U=Yt(_),P=[{type:12,data:m},{type:12,data:U},{type:12,data:_},{type:1,data:r.epsilon}],F=K=>{let ee=[{name:"output_size",type:"u32"},{name:"components",type:"u32"},{name:"hidden_size",type:"u32"},{name:"epsilon",type:"f32"}],ae=[$e("x",e[0].dataType,e[0].dims,U),$e("skip",e[1].dataType,e[1].dims,U),$e("gamma",e[2].dataType,e[2].dims,U)];x&&ae.push($e("beta",e[3].dataType,e[3].dims,U)),T&&ae.push($e("bias",e[4].dataType,e[4].dims,U)),ae.push(je("output",e[0].dataType,g,U)),C&&ae.push(je("mean_output",1,v)),A&&ae.push(je("inv_std_output",1,v)),R&&ae.push(je("input_skip_bias_sum",e[0].dataType,g,U));let B=yr(e[0].dataType),me=yr(1,U);return`

      ${K.registerUniforms(ee).declareVariables(...ae)}
      var<workgroup> sum_shared : array<${me}, ${H}>;
      var<workgroup> sum_squared_shared : array<${me}, ${H}>;

      ${K.mainStart([H,1,1])}
        let ix = local_id.x;
        let iy = global_id.x / ${H};

        let hidden_size_vectorized: u32 = uniforms.hidden_size / uniforms.components;
        var stride = hidden_size_vectorized / ${H};
        let offset = ix * stride + iy * hidden_size_vectorized;
        let offset1d = stride * ix;
        if (ix == ${H-1}) {
          stride = hidden_size_vectorized - stride * ix;
        }
        for (var i: u32 = 0; i < stride; i++) {
          let skip_value = skip[offset + i];
          let bias_value = ${T?"bias[offset1d + i]":B+"(0.0)"};
          let input_value = x[offset + i];
          let value = input_value + skip_value + bias_value;
          ${R?"input_skip_bias_sum[offset + i] = value;":""}
          output[offset + i] = value;
          let f32_value = ${fs(B,U,"value")};
          sum_shared[ix] += f32_value;
          sum_squared_shared[ix] += f32_value * f32_value;
        }
        workgroupBarrier();

        var reduce_size : u32 = ${H};
        for (var curr_size = reduce_size >> 1;  curr_size > 0; curr_size = reduce_size >> 1) {
          reduce_size = curr_size + (reduce_size & 1);
          if (ix < curr_size) {
            sum_shared[ix] += sum_shared[ix + reduce_size];
            sum_squared_shared[ix] += sum_squared_shared[ix + reduce_size];
          }
          workgroupBarrier();
        }

        let sum = sum_shared[0];
        let square_sum = sum_squared_shared[0];
        let mean = ${an("sum",U)} / f32(uniforms.hidden_size);
        let inv_std_dev = inverseSqrt(${an("square_sum",U)} / f32(uniforms.hidden_size) ${o?"":"- mean * mean"} + uniforms.epsilon);
        ${C?"mean_output[global_idx] = mean;":""}
        ${A?"inv_std_output[global_idx] = inv_std_dev;":""}

        for (var i: u32 = 0; i < stride; i++) {
          output[offset + i] = (output[offset + i] ${o?"":`- ${B}(mean)`}) *
            ${B}(inv_std_dev) * gamma[offset1d + i]
            ${x?"+ beta[offset1d + i]":""};
        }
      }`},G=[{dims:g,dataType:e[0].dataType}];return a>1&&G.push({dims:v,dataType:1}),a>2&&G.push({dims:v,dataType:1}),a>3&&G.push({dims:p,dataType:e[0].dataType}),{name:"SkipLayerNormalization",shaderCache:{hint:`${U};${C};${A};${R}`,inputDependencies:e.map((K,ee)=>"type")},getShaderSource:F,getRunData:()=>({outputs:G,dispatchGroup:{x:Math.ceil(m/_)},programUniforms:P})}},ev=(e,r)=>{mw(e.inputs);let a=[0];e.outputCount>1&&a.push(-3),e.outputCount>2&&a.push(-3),e.outputCount>3&&a.push(3),e.compute(gw(e.inputs,r,e.outputCount,!1),{outputs:a})}}),yw,Mo,_w,Yd,ww,bw,tv,rv,N1=Ee(()=>{ut(),ct(),Jt(),ft(),yw=(e,r)=>{if(!e||e.length<1)throw new Error("too few inputs");if(r.axes.length!==0){if(r.axes.length!==r.starts.length||r.axes.length!==r.ends.length)throw new Error("axes, starts and ends must have the same length")}else if(r.starts.length!==r.ends.length)throw new Error("starts and ends must have the same length");e.slice(1).forEach((a,s)=>{if(e[s+1].dataType!==6&&e[s+1].dataType!==7)throw new Error(`Input ${s} must be an array of int32 or int64`)})},Mo=(e,r)=>{let a=[];if(e.length>r)if(e[r].dataType===7)e[r].getBigInt64Array().forEach(s=>a.push(Number(s)));else if(e[r].dataType===6)e[r].getInt32Array().forEach(s=>a.push(Number(s)));else throw new Error(`Input ${r} must be an array of int32 or int64`);return a},_w=(e,r)=>{if(e.length>1){let a=Mo(e,1),s=Mo(e,2),o=Mo(e,3);return o.length===0&&(o=[...Array(e[0].dims.length).keys()]),Nt({starts:a,ends:s,axes:o})}else return r},Yd=(e,r,a,s,o)=>{let p=e;return e<0&&(p+=a[s[r]]),o[r]<0?Math.max(0,Math.min(p,a[s[r]]-1)):Math.max(0,Math.min(p,a[s[r]]))},ww=(e,r,a)=>`fn calculateInputIndices(output_indices: ${r.type.indices}) -> ${e.type.indices} {
          var input_indices: ${e.type.indices};
          var carry = 0u;
          for (var i = ${a.length-1}; i >= 0; i--) {
            let input_shape_i = ${Qe("uniforms.input_shape","i",a.length)};
            let steps_i = ${Qe("uniforms.steps","i",a.length)};
            let signs_i = ${Qe("uniforms.signs","i",a.length)};
            let starts_i = ${Qe("uniforms.starts","i",a.length)};
            var output_index = ${r.indicesGet("output_indices","i")};
            var input_index = output_index * steps_i + starts_i + carry;
            carry = input_index / input_shape_i;
            input_index = input_index % input_shape_i;
            if (signs_i < 0) {
              input_index = input_shape_i - input_index - 1u + starts_i;
            }
            ${e.indicesSet("input_indices","i","input_index")};
          }
          return input_indices;
      }`,bw=(e,r)=>{let a=e[0].dims,s=ge.size(a),o=r.axes.length>0?ge.normalizeAxes(r.axes,a.length):[...Array(a.length).keys()],p=Mo(e,4);p.forEach(U=>U!==0||(()=>{throw new Error("step cannot be 0")})),p.length===0&&(p=Array(o.length).fill(1));let d=r.starts.map((U,P)=>Yd(U,P,a,o,p)),g=r.ends.map((U,P)=>Yd(U,P,a,o,p));if(o.length!==d.length||o.length!==g.length)throw new Error("start, ends and axes should have the same number of elements");if(o.length!==a.length)for(let U=0;U<a.length;++U)o.includes(U)||(d.splice(U,0,0),g.splice(U,0,a[U]),p.splice(U,0,1));let m=p.map(U=>Math.sign(U));p.forEach((U,P,F)=>{if(U<0){let G=(g[P]-d[P])/U,K=d[P],ee=K+G*p[P];d[P]=ee,g[P]=K,F[P]=-U}});let _=a.slice(0);o.forEach((U,P)=>{_[U]=Math.ceil((g[U]-d[U])/p[U])});let v={dims:_,dataType:e[0].dataType},x=je("output",e[0].dataType,_.length),T=$e("input",e[0].dataType,e[0].dims.length),C=ge.size(_),A=[{name:"outputSize",type:"u32"},{name:"starts",type:"u32",length:d.length},{name:"signs",type:"i32",length:m.length},{name:"steps",type:"u32",length:p.length}],R=[{type:12,data:C},{type:12,data:d},{type:6,data:m},{type:12,data:p},...Je(e[0].dims,_)],H=U=>`
      ${U.registerUniforms(A).declareVariables(T,x)}
        ${ww(T,x,a)}
        ${U.mainStart()}
          ${U.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.outputSize")}
          let output_indices = ${x.offsetToIndices("global_idx")};
          let input_indices = calculateInputIndices(output_indices);
          ${x.setByOffset("global_idx",T.getByIndices("input_indices"))}
      }`;return{name:"Slice",shaderCache:{hint:`${m.length}_${d.length}_${p.length}`,inputDependencies:["rank"]},getShaderSource:H,getRunData:()=>({outputs:[v],dispatchGroup:{x:Math.ceil(s/64)},programUniforms:R})}},tv=(e,r)=>{yw(e.inputs,r);let a=_w(e.inputs,r);e.compute(bw(e.inputs,a),{inputs:[0]})},rv=e=>{let r=e.starts,a=e.ends,s=e.axes;return Nt({starts:r,ends:a,axes:s})}}),$w,vw,iv,av,P1=Ee(()=>{ut(),ct(),Jt(),nn(),ft(),$w=e=>{if(!e||e.length!==1)throw new Error("Softmax op requires 1 input.")},vw=(e,r)=>{let a=e.inputs[0],s=a.dims,o=ge.size(s),p=s.length,d=ge.normalizeAxis(r.axis,p),g=d<s.length-1,m,_=[];g?(_=Array.from({length:p},(ae,B)=>B),_[d]=p-1,_[p-1]=d,m=e.compute(si(a,_),{inputs:[a],outputs:[-1]})[0]):m=a;let v=m.dims,x=v[p-1],T=o/x,C=Yt(x),A=x/C,R=64;T===1&&(R=256);let H=(ae,B)=>B===4?`max(max(${ae}.x, ${ae}.y), max(${ae}.z, ${ae}.w))`:B===2?`max(${ae}.x, ${ae}.y)`:B===3?`max(max(${ae}.x, ${ae}.y), ${ae}.z)`:ae,U=$e("x",m.dataType,m.dims,C),P=je("result",m.dataType,m.dims,C),F=U.type.value,G=yr(m.dataType)==="f32"?`var threadMax = ${F}(-3.4028234663852886e+38f);`:`var threadMax = ${F}(-65504.0h);`,K=ae=>`
      var<workgroup> rowMaxShared : ${F};
      var<workgroup> rowSumShared : ${F};
      var<workgroup> threadShared : array<${F}, ${R}>;

      fn getValue(row: i32, col: i32, row_stride: i32) -> ${F} {
        let index = row * row_stride + col;
        return x[index];
      }

      fn setValue(row: i32, col: i32, row_stride: i32, value: ${F}) {
        let index = row * row_stride + col;
        result[index] = value;
      }
      ${ae.registerUniform("packedCols","i32").declareVariables(U,P)}
      ${ae.mainStart(R)}
        let gindex = i32(global_idx);
        let lindex = i32(local_idx);
        const wg = ${R};
        let row = gindex / wg;
        let cols = uniforms.packedCols;
        let row_stride : i32 = uniforms.packedCols;

        // find the rows max
        ${G}
        for (var col = lindex; col < cols; col += wg) {
          let value = getValue(row, col, row_stride);
          threadMax = max(threadMax, value);
        }
        if (lindex < cols) {
          threadShared[lindex] = threadMax;
        }
        workgroupBarrier();

        var reduceSize = min(cols, wg);
        for (var currSize = reduceSize >> 1;  currSize > 0; currSize = reduceSize >> 1) {
          reduceSize = currSize + (reduceSize & 1);
          if (lindex < currSize) {
            threadShared[lindex] = max(threadShared[lindex], threadShared[lindex + reduceSize]);
          }
          workgroupBarrier();
        }
        if (lindex == 0) {
          rowMaxShared = ${F}(${H("threadShared[0]",C)});
        }
        workgroupBarrier();

        // find the rows sum
        var threadSum = ${F}(0.0);
        for (var col = lindex; col < cols; col += wg) {
          let subExp = exp(getValue(row, col, row_stride) - rowMaxShared);
          threadSum += subExp;
        }
        threadShared[lindex] = threadSum;
        workgroupBarrier();

        for (var currSize = wg >> 1;  currSize > 0; currSize = currSize >> 1) {
          if (lindex < currSize) {
            threadShared[lindex] = threadShared[lindex] + threadShared[lindex + currSize];
          }
          workgroupBarrier();
        }
        if (lindex == 0) {
          rowSumShared = ${F}(${an("threadShared[0]",C)});
        }
        workgroupBarrier();

        // calculate final value for each element in the row
        for (var col = lindex; col < cols; col += wg) {
          var value = exp(getValue(row, col, row_stride) - rowMaxShared) / rowSumShared;
          // max operation protects against NaN since all values should be >=0
          value = max(value, ${F}(0.0));
          setValue(row, col, row_stride, value);
        }
      }`,ee=e.compute({name:"Softmax",shaderCache:{hint:`${C};${R}`,inputDependencies:["type"]},getRunData:()=>({outputs:[{dims:v,dataType:m.dataType}],dispatchGroup:{x:T},programUniforms:[{type:6,data:A}]}),getShaderSource:K},{inputs:[m],outputs:[g?-1:0]})[0];g&&e.compute(si(ee,_),{inputs:[ee]})},iv=(e,r)=>{$w(e.inputs),vw(e,r)},av=e=>Nt({axis:e.axis})}),Jd,xw,Sw,Tw,nv,U1=Ee(()=>{ut(),ct(),ft(),Jd=e=>Array.from(e.getBigInt64Array(),Number),xw=e=>{if(!e||e.length!==2)throw new Error("Tile requires 2 inputs.");if(e[0].dataType!==1&&e[0].dataType!==10&&e[0].dataType!==6&&e[0].dataType!==12)throw new Error("Tile only support float, float16, int32, and uint32 data types");if(e[1].dataType!==7)throw new Error("Tile `repeats` input should be of int64 data type");if(e[1].dims.length!==1)throw new Error("Tile `repeats` input should be 1-D");if(Jd(e[1]).length!==e[0].dims.length)throw new Error("Tile `repeats` input should have same number of elements as rank of input data tensor")},Sw=(e,r)=>{let a=[];for(let s=0;s<e.length;++s)a.push(e[s]*r[s]);return a},Tw=(e,r)=>{let a=e[0].dims,s=r??Jd(e[1]),o=Sw(a,s),p=ge.size(o),d=e[0].dataType,g=$e("input",d,a.length),m=je("output",d,o.length),_=v=>`
      const inputShape = ${g.indices(...a)};
      ${v.registerUniform("output_size","u32").declareVariables(g,m)}
      ${v.mainStart()}
      ${v.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.output_size")}
      let output_indices = ${m.offsetToIndices("global_idx")};
      var input_indices: ${g.type.indices};
      for (var i = 0; i < ${a.length}; i++) {
        let input_dim_i = ${g.indicesGet("uniforms.input_shape","i")};
        let input_dim_value = ${m.indicesGet("output_indices","i")}  % input_dim_i;

        ${g.indicesSet("input_indices","i","input_dim_value")}
      }
      ${m.setByOffset("global_idx",g.getByIndices("input_indices"))}
    }`;return{name:"Tile",shaderCache:{hint:`${s}`,inputDependencies:["rank"]},getRunData:()=>({outputs:[{dims:o,dataType:e[0].dataType}],dispatchGroup:{x:Math.ceil(p/64)},programUniforms:[{type:12,data:p},...Je(e[0].dims,o)]}),getShaderSource:_}},nv=e=>{xw(e.inputs),e.compute(Tw(e.inputs),{inputs:[0]})}}),kw,Ew,sv,L1=Ee(()=>{ut(),ct(),ft(),kw=(e,r,a,s,o)=>{let p=je("output_data",o,a.length,4),d=$e("a_data",r[1].dataType,r[1].dims.length,4),g=$e("b_data",r[2].dataType,r[2].dims.length,4),m=$e("c_data",r[0].dataType,r[0].dims.length,4),_,v=(x,T,C)=>`select(${T}, ${x}, ${C})`;if(!s)_=p.setByOffset("global_idx",v(d.getByOffset("global_idx"),g.getByOffset("global_idx"),m.getByOffset("global_idx")));else{let x=(T,C,A="")=>{let R=`a_data[index_a${C}][component_a${C}]`,H=`b_data[index_b${C}][component_b${C}]`,U=`bool(c_data[index_c${C}] & (0xffu << (component_c${C} * 8)))`;return`
            let output_indices${C} = ${p.offsetToIndices(`global_idx * 4u + ${C}u`)};
            let offset_a${C} = ${d.broadcastedIndicesToOffset(`output_indices${C}`,p)};
            let offset_b${C} = ${g.broadcastedIndicesToOffset(`output_indices${C}`,p)};
            let offset_c${C} = ${m.broadcastedIndicesToOffset(`output_indices${C}`,p)};
            let index_a${C} = offset_a${C} / 4u;
            let index_b${C} = offset_b${C} / 4u;
            let index_c${C} = offset_c${C} / 4u;
            let component_a${C} = offset_a${C} % 4u;
            let component_b${C} = offset_b${C} % 4u;
            let component_c${C} = offset_c${C} % 4u;
            ${T}[${C}] = ${A}(${v(R,H,U)});
          `};o===9?_=`
            var data = vec4<u32>(0);
            ${x("data",0,"u32")}
            ${x("data",1,"u32")}
            ${x("data",2,"u32")}
            ${x("data",3,"u32")}
            output_data[global_idx] = dot(vec4<u32>(0x1, 0x100, 0x10000, 0x1000000), vec4<u32>(data));`:_=`
            ${x("output_data[global_idx]",0)}
            ${x("output_data[global_idx]",1)}
            ${x("output_data[global_idx]",2)}
            ${x("output_data[global_idx]",3)}
          `}return`
        ${e.registerUniform("vec_size","u32").declareVariables(m,d,g,p)}
        ${e.mainStart()}
        ${e.guardAgainstOutOfBoundsWorkgroupSizes("uniforms.vec_size")}
        ${_}
      }`},Ew=e=>{let r=e[1].dims,a=e[2].dims,s=e[0].dims,o=e[1].dataType,p=!(ge.areEqual(r,a)&&ge.areEqual(a,s)),d=r,g=ge.size(r);if(p){let _=ms.calcShape(ms.calcShape(r,a,!1),s,!1);if(!_)throw new Error("Can't perform where op on the given tensors");d=_,g=ge.size(d)}let m=Math.ceil(g/4);return{name:"Where",shaderCache:{inputDependencies:["rank","rank","rank"]},getShaderSource:_=>kw(_,e,d,p,o),getRunData:()=>({outputs:[{dims:d,dataType:o}],dispatchGroup:{x:Math.ceil(g/64/4)},programUniforms:[{type:12,data:m},...Je(s,r,a,d)]})}},sv=e=>{e.compute(Ew(e.inputs))}}),ov,q1=Ee(()=>{t1(),Dp(),r1(),i1(),a1(),n1(),s1(),p1(),h1(),f1(),m1(),g1(),y1(),_1(),w1(),b1(),$1(),v1(),x1(),S1(),T1(),k1(),E1(),I1(),z1(),k$(),C1(),A1(),O1(),R1(),B1(),Mp(),M1(),A$(),D1(),N1(),P1(),z$(),U1(),nn(),Np(),L1(),ov=new Map([["Abs",[eb]],["Acos",[tb]],["Acosh",[rb]],["Add",[Db]],["ArgMax",[Q0,dp]],["ArgMin",[Z0,dp]],["Asin",[ib]],["Asinh",[ab]],["Atan",[nb]],["Atanh",[sb]],["Attention",[X0]],["AveragePool",[L$,U$]],["BatchNormalization",[Y0]],["BiasAdd",[J0]],["BiasSplitGelu",[Mb]],["Cast",[ub,ob]],["Ceil",[db]],["Clip",[lb]],["Concat",[Hb,jb]],["Conv",[gp,mp]],["ConvTranspose",[i$,r$]],["Cos",[pb]],["Cosh",[cb]],["CumSum",[a$,n$]],["DepthToSpace",[s$,o$]],["DequantizeLinear",[j$,K$]],["Div",[Nb]],["Einsum",[u$,l$]],["Elu",[hb,qo]],["Equal",[Pb]],["Erf",[fb]],["Exp",[mb]],["Expand",[d$]],["FastGelu",[p$]],["Floor",[gb]],["FusedConv",[gp,mp]],["Gather",[h$,c$]],["GatherElements",[w$,_$]],["GatherBlockQuantized",[g$,y$]],["GatherND",[f$,m$]],["Gelu",[yb]],["Gemm",[$$,b$]],["GlobalAveragePool",[V$,q$]],["GlobalMaxPool",[H$,F$]],["Greater",[Vb]],["GreaterOrEqual",[Gb]],["GridSample",[v$,x$]],["GroupQueryAttention",[O$]],["HardSigmoid",[Tb,Sb]],["InstanceNormalization",[R$]],["LayerNormalization",[B$]],["LeakyRelu",[_b,qo]],["Less",[Wb]],["LessOrEqual",[Fb]],["Log",[Rb]],["MatMul",[M$]],["MatMulNBits",[D$,N$]],["MaxPool",[W$,G$]],["Mul",[Ub]],["MultiHeadAttention",[T$,S$]],["Neg",[bb]],["Not",[wb]],["Pad",[P$]],["Pow",[Lb]],["QuickGelu",[Bb,qo]],["Range",[Z$]],["Reciprocal",[$b]],["ReduceMin",[G0]],["ReduceMean",[U0]],["ReduceMax",[W0]],["ReduceSum",[H0]],["ReduceProd",[F0]],["ReduceL1",[L0]],["ReduceL2",[q0]],["ReduceLogSum",[K0]],["ReduceLogSumExp",[V0]],["ReduceSumSquare",[j0]],["Relu",[vb]],["Resize",[Y$,J$]],["RotaryEmbedding",[C$]],["ScatterND",[X$,Q$]],["Sigmoid",[xb]],["Sin",[kb]],["Sinh",[Eb]],["Slice",[tv,rv]],["SkipLayerNormalization",[ev]],["Split",[E$,I$]],["Sqrt",[Ib]],["Softmax",[iv,av]],["Sub",[qb]],["Tan",[zb]],["Tanh",[Cb]],["ThresholdedRelu",[Ob,qo]],["Tile",[nv]],["Transpose",[E0,I0]],["Where",[sv]]])}),uv,V1=Ee(()=>{bi(),Ta(),ft(),uv=class{constructor(e){this.backend=e,this.repo=new Map,this.attributesBound=!1}getArtifact(e){return this.repo.get(e)}setArtifact(e,r){this.repo.set(e,r)}run(e,r,a,s,o){Xi(e.programInfo.name);let p=this.backend.device,d=this.backend.getComputePassEncoder();this.backend.writeTimestamp(this.backend.pendingDispatchNumber*2);let g=[];for(let _ of r)g.push({binding:g.length,resource:{buffer:_.buffer}});for(let _ of a)g.push({binding:g.length,resource:{buffer:_.buffer}});o&&g.push({binding:g.length,resource:o});let m=p.createBindGroup({layout:e.computePipeline.getBindGroupLayout(0),entries:g,label:e.programInfo.name});if(this.backend.sessionStatus==="capturing"){let _={kernelId:this.backend.currentKernelId,computePipeline:e.computePipeline,bindGroup:m,dispatchGroup:s};this.backend.capturedCommandList.get(this.backend.currentSessionId).push(_)}d.setPipeline(e.computePipeline),d.setBindGroup(0,m),d.dispatchWorkgroups(...s),this.backend.writeTimestamp(this.backend.pendingDispatchNumber*2+1),this.backend.pendingDispatchNumber++,(this.backend.pendingDispatchNumber>=this.backend.maxDispatchNumber||this.backend.queryType==="at-passes")&&this.backend.endComputePass(),this.backend.pendingDispatchNumber>=this.backend.maxDispatchNumber&&this.backend.flush(),Pi(e.programInfo.name)}dispose(){}build(e,r){Xi(e.name);let a=this.backend.device,s=[];[{feature:"shader-f16",extension:"f16"},{feature:"subgroups",extension:"subgroups"}].forEach(_=>{a.features.has(_.feature)&&s.push(`enable ${_.extension};`)});let o=k0(r,this.backend.device.limits),p=e.getShaderSource(o),d=`${s.join(`
`)}
${o.additionalImplementations}
${p}`,g=a.createShaderModule({code:d,label:e.name});It("verbose",()=>`[WebGPU] ${e.name} shader code: ${d}`);let m=a.createComputePipeline({compute:{module:g,entryPoint:"main"},layout:"auto",label:e.name});return Pi(e.name),{programInfo:e,computePipeline:m,uniformVariablesInfo:o.variablesInfo}}normalizeDispatchGroupSize(e){let r=typeof e=="number"?e:e.x,a=typeof e=="number"?1:e.y||1,s=typeof e=="number"?1:e.z||1,o=this.backend.device.limits.maxComputeWorkgroupsPerDimension;if(r<=o&&a<=o&&s<=o)return[r,a,s];let p=r*a*s,d=Math.ceil(Math.sqrt(p));if(d>o){if(d=Math.ceil(Math.cbrt(p)),d>o)throw new Error("Total dispatch size exceeds WebGPU maximum.");return[d,d,d]}else return[d,d,1]}}}),lv={};ys(lv,{WebGpuBackend:()=>dv});var Iw,zw,Cw,dv,W1=Ee(()=>{bi(),ut(),Ta(),$0(),J2(),q1(),V1(),Iw=(e,r)=>{if(r.length!==e.length)throw new Error(`inputDependencies length ${r.length} is not equal to inputTensors length ${e.length}.`);let a=[];for(let s=0;s<e.length;++s){let o=e[s].dataType;switch(r[s]){case"none":{a.push("");break}case"type":{a.push(`${o}`);break}case"rank":{let p=e[s].dims.length;a.push(`${o};${p}`);break}case"dims":{let p=e[s].dims.join(",");a.push(`${o};${p}`);break}default:throw new Error(`unsupported input dependency: ${r[s]}`)}}return a.join("|")},zw=(e,r,a)=>{var o,p;let s=e.name;return(o=e.shaderCache)!=null&&o.hint&&(s+="["+e.shaderCache.hint+"]"),s+=":"+a+`:${Iw(r,((p=e.shaderCache)==null?void 0:p.inputDependencies)??new Array(r.length).fill("dims"))}`,s},Cw=class{constructor(e){e&&(this.architecture=e.architecture,this.vendor=e.vendor)}isArchitecture(e){return this.architecture===e}isVendor(e){return this.vendor===e}},dv=class{constructor(){this.currentSessionId=null,this.currentKernelId=null,this.commandEncoder=null,this.computePassEncoder=null,this.maxDispatchNumber=16,this.pendingDispatchNumber=0,this.pendingKernels=[],this.pendingQueries=new Map,this.sessionStatus="default",this.capturedCommandList=new Map,this.capturedPendingKernels=new Map,this.sessionExternalDataMapping=new Map}get currentKernelCustomData(){if(this.currentKernelId===null)throw new Error("currentKernelCustomData(): currentKernelId is null. (should not happen)");let e=this.kernelCustomData.get(this.currentKernelId);return e||(e={},this.kernelCustomData.set(this.currentKernelId,e)),e}async initialize(e,r){this.env=e;let a=[],s={requiredLimits:{maxComputeWorkgroupStorageSize:r.limits.maxComputeWorkgroupStorageSize,maxComputeWorkgroupsPerDimension:r.limits.maxComputeWorkgroupsPerDimension,maxStorageBufferBindingSize:r.limits.maxStorageBufferBindingSize,maxBufferSize:r.limits.maxBufferSize,maxComputeInvocationsPerWorkgroup:r.limits.maxComputeInvocationsPerWorkgroup,maxComputeWorkgroupSizeX:r.limits.maxComputeWorkgroupSizeX,maxComputeWorkgroupSizeY:r.limits.maxComputeWorkgroupSizeY,maxComputeWorkgroupSizeZ:r.limits.maxComputeWorkgroupSizeZ},requiredFeatures:a},o=p=>r.features.has(p)&&a.push(p)&&!0;o("chromium-experimental-timestamp-query-inside-passes")||o("timestamp-query"),o("shader-f16"),o("subgroups"),this.device=await r.requestDevice(s),this.adapterInfo=new Cw(r.info||await r.requestAdapterInfo()),this.gpuDataManager=S0(this),this.programManager=new uv(this),this.kernels=new Map,this.kernelPersistentData=new Map,this.kernelCustomData=new Map,Ap(e.logLevel,!!e.debug),this.device.onuncapturederror=p=>{p.error instanceof GPUValidationError&&console.error(`An uncaught WebGPU validation error was raised: ${p.error.message}`)},Object.defineProperty(this.env.webgpu,"device",{value:this.device,writable:!1,enumerable:!0,configurable:!0}),Object.defineProperty(this.env.webgpu,"adapter",{value:r,writable:!1,enumerable:!0,configurable:!1}),this.setQueryType()}dispose(){var e;typeof this.querySet<"u"&&this.querySet.destroy(),this.gpuDataManager.dispose(),this.device&&((e=this.env)!=null&&e.webgpu)&&this.device.lost.then(()=>{delete this.env.webgpu.device})}getCommandEncoder(){return this.commandEncoder||(this.commandEncoder=this.device.createCommandEncoder()),this.commandEncoder}getComputePassEncoder(){if(!this.computePassEncoder){let e=this.getCommandEncoder(),r={};this.queryType==="at-passes"&&(r.timestampWrites={querySet:this.querySet,beginningOfPassWriteIndex:this.pendingDispatchNumber*2,endOfPassWriteIndex:this.pendingDispatchNumber*2+1}),this.computePassEncoder=e.beginComputePass(r)}return this.computePassEncoder}endComputePass(){this.computePassEncoder&&(this.computePassEncoder.end(),this.computePassEncoder=null)}flush(){if(!this.commandEncoder)return;Xi(),this.endComputePass();let e;this.queryType!=="none"&&(this.commandEncoder.resolveQuerySet(this.querySet,0,this.pendingDispatchNumber*2,this.queryResolveBuffer,0),e=this.device.createBuffer({size:this.pendingDispatchNumber*2*8,usage:GPUBufferUsage.MAP_READ|GPUBufferUsage.COPY_DST}),this.pendingQueries.set(e,this.pendingKernels),this.pendingKernels=[],this.commandEncoder.copyBufferToBuffer(this.queryResolveBuffer,0,e,0,this.pendingDispatchNumber*2*8)),this.device.queue.submit([this.commandEncoder.finish()]),this.gpuDataManager.refreshPendingBuffers(),this.commandEncoder=null,this.pendingDispatchNumber=0,this.queryType!=="none"&&e.mapAsync(GPUMapMode.READ).then(()=>{var s;let r=new BigUint64Array(e.getMappedRange()),a=this.pendingQueries.get(e);for(let o=0;o<r.length/2;o++){let p=a[o],d=p.kernelId,g=this.kernels.get(d),m=g.kernelType,_=g.kernelName,v=p.programName,x=p.inputTensorViews,T=p.outputTensorViews,C=r[o*2],A=r[o*2+1];typeof this.queryTimeBase>"u"&&(this.queryTimeBase=C);let R=Number(C-this.queryTimeBase),H=Number(A-this.queryTimeBase);if(!Number.isSafeInteger(R)||!Number.isSafeInteger(H))throw new RangeError("incorrect timestamp range");if((s=this.env.webgpu.profiling)!=null&&s.ondata)this.env.webgpu.profiling.ondata({version:1,inputsMetadata:x.map(U=>({dims:U.dims,dataType:Sa(U.dataType)})),outputsMetadata:T.map(U=>({dims:U.dims,dataType:Sa(U.dataType)})),kernelId:d,kernelType:m,kernelName:_,programName:v,startTime:R,endTime:H});else{let U="";x.forEach((F,G)=>{U+=`input[${G}]: [${F.dims}] | ${Sa(F.dataType)}, `});let P="";T.forEach((F,G)=>{P+=`output[${G}]: [${F.dims}] | ${Sa(F.dataType)}, `}),console.log(`[profiling] kernel "${d}|${m}|${_}|${v}" ${U}${P}start time: ${R} ns, execution time: ${H-R} ns`)}tl("GPU",`${v}::${C}::${A}`)}e.unmap(),this.pendingQueries.delete(e)}),Pi()}run(e,r,a,s,o,p){Xi(e.name);let d=[];for(let P=0;P<r.length;++P){let F=r[P].data;if(F===0)continue;let G=this.gpuDataManager.get(F);if(!G)throw new Error(`no GPU data for input: ${F}`);d.push(G)}let{outputs:g,dispatchGroup:m,programUniforms:_}=e.getRunData(r),v=a.length===0?g.map((P,F)=>F):a;if(v.length!==g.length)throw new Error(`Output size ${v.length} must be equal to ${g.length}.`);let x=[],T=[];for(let P=0;P<g.length;++P){if(!Number.isInteger(v[P])||v[P]<-3||v[P]>=p)throw new Error(`Invalid output index: ${v[P]}`);if(v[P]===-3)continue;let F=v[P]===-1,G=v[P]===-2,K=F||G?o(g[P].dataType,g[P].dims):s(v[P],g[P].dataType,g[P].dims);if(x.push(K),K.data===0)continue;let ee=this.gpuDataManager.get(K.data);if(!ee)throw new Error(`no GPU data for output: ${K.data}`);if(F&&this.temporaryData.push(ee),G){let ae=this.kernelPersistentData.get(this.currentKernelId);ae||(ae=[],this.kernelPersistentData.set(this.currentKernelId,ae)),ae.push(ee)}T.push(ee)}if(d.length!==r.length||T.length!==x.length){if(T.length===0)return Pi(e.name),x;throw new Error(`Program ${e.name} has zero-sized tensor(s) in inputs or outputs. This is not supported now.`)}let C;if(_){let P=0,F=[];_.forEach(ae=>{let B=typeof ae.data=="number"?[ae.data]:ae.data;if(B.length===0)return;let me=ae.type===10?2:4,_e,Re;ae.type===10?(Re=B.length>4?16:B.length>2?8:B.length*me,_e=B.length>4?16:me*B.length):(Re=B.length<=2?B.length*me:16,_e=16),P=Math.ceil(P/Re)*Re,F.push(P);let Ue=ae.type===10?8:4;P+=B.length>4?Math.ceil(B.length/Ue)*_e:B.length*me});let G=16;P=Math.ceil(P/G)*G;let K=new ArrayBuffer(P);_.forEach((ae,B)=>{let me=F[B],_e=typeof ae.data=="number"?[ae.data]:ae.data;if(ae.type===6)new Int32Array(K,me,_e.length).set(_e);else if(ae.type===12)new Uint32Array(K,me,_e.length).set(_e);else if(ae.type===10)new Uint16Array(K,me,_e.length).set(_e);else if(ae.type===1)new Float32Array(K,me,_e.length).set(_e);else throw new Error(`Unsupported uniform type: ${Sa(ae.type)}`)});let ee=this.gpuDataManager.create(P,GPUBufferUsage.COPY_DST|GPUBufferUsage.UNIFORM);this.device.queue.writeBuffer(ee.buffer,0,K,0,P),this.gpuDataManager.release(ee.id),C={offset:0,size:P,buffer:ee.buffer}}let A=this.programManager.normalizeDispatchGroupSize(m),R=A[1]===1&&A[2]===1,H=zw(e,r,R),U=this.programManager.getArtifact(H);if(U||(U=this.programManager.build(e,A),this.programManager.setArtifact(H,U),It("info",()=>`[artifact] key: ${H}, programName: ${e.name}`)),_&&U.uniformVariablesInfo){if(_.length!==U.uniformVariablesInfo.length)throw new Error(`Uniform variables count mismatch: expect ${U.uniformVariablesInfo.length}, got ${_.length} in program "${U.programInfo.name}".`);for(let P=0;P<_.length;P++){let F=_[P],G=F.type,K=typeof F.data=="number"?1:F.data.length,[ee,ae]=U.uniformVariablesInfo[P];if(G!==ee||K!==ae)throw new Error(`Uniform variable ${P} mismatch: expect type ${ee} with size ${ae}, got type ${G} with size ${K} in program "${U.programInfo.name}".`)}}if(It("info",()=>`[ProgramManager] run "${e.name}" (key=${H}) with ${A[0]}x${A[1]}x${A[2]}`),this.queryType!=="none"||this.sessionStatus==="capturing"){let P={kernelId:this.currentKernelId,programName:U.programInfo.name,inputTensorViews:r,outputTensorViews:x};this.pendingKernels.push(P),this.sessionStatus==="capturing"&&this.capturedPendingKernels.get(this.currentSessionId).push(P)}return this.programManager.run(U,d,T,A,C),Pi(e.name),x}upload(e,r){this.gpuDataManager.upload(e,r)}memcpy(e,r){this.gpuDataManager.memcpy(e,r)}async download(e,r){await this.gpuDataManager.download(e,r)}alloc(e){return this.gpuDataManager.create(e).id}free(e){return this.gpuDataManager.release(e)}createKernel(e,r,a,s){let o=ov.get(e);if(!o)throw new Error(`kernel not implemented: ${e}`);let p={kernelType:e,kernelName:s,kernelEntry:o[0],attributes:[o[1],a]};this.kernels.set(r,p)}releaseKernel(e){let r=this.kernelPersistentData.get(e);if(r){for(let a of r)this.gpuDataManager.release(a.id);this.kernelPersistentData.delete(e)}this.kernelCustomData.delete(e),this.kernels.delete(e)}computeKernel(e,r,a){let s=this.kernels.get(e);if(!s)throw new Error(`kernel not created: ${e}`);let o=s.kernelType,p=s.kernelName,d=s.kernelEntry,g=s.attributes;if(this.currentKernelId!==null)throw new Error(`kernel "[${o}] ${p}" is not allowed to be called recursively`);this.currentKernelId=e,g[0]&&(g[1]=g[0](g[1]),g[0]=void 0),It("info",()=>`[WebGPU] Start to run kernel "[${o}] ${p}"...`);let m=this.env.debug;this.temporaryData=[];try{return m&&this.device.pushErrorScope("validation"),d(r,g[1]),0}catch(_){return a.push(Promise.resolve(`[WebGPU] Kernel "[${o}] ${p}" failed. ${_}`)),1}finally{m&&a.push(this.device.popErrorScope().then(_=>_?`GPU validation error for kernel "[${o}] ${p}": ${_.message}`:null));for(let _ of this.temporaryData)this.gpuDataManager.release(_.id);this.temporaryData=[],this.currentKernelId=null}}registerBuffer(e,r,a,s){let o=this.sessionExternalDataMapping.get(e);o||(o=new Map,this.sessionExternalDataMapping.set(e,o));let p=o.get(r),d=this.gpuDataManager.registerExternalBuffer(a,s,p);return o.set(r,[d,a]),d}unregisterBuffers(e){let r=this.sessionExternalDataMapping.get(e);r&&(r.forEach(a=>this.gpuDataManager.unregisterExternalBuffer(a[0])),this.sessionExternalDataMapping.delete(e))}getBuffer(e){let r=this.gpuDataManager.get(e);if(!r)throw new Error(`no GPU data for buffer: ${e}`);return r.buffer}createDownloader(e,r,a){return async()=>{let s=await op(this,e,r);return Op(s.buffer,a)}}writeTimestamp(e){this.queryType==="inside-passes"&&this.computePassEncoder.writeTimestamp(this.querySet,e)}setQueryType(){var e;this.queryType="none",(((e=this.env.webgpu.profiling)==null?void 0:e.mode)==="default"||(typeof this.env.trace>"u"?this.env.wasm.trace:this.env.trace))&&(this.device.features.has("chromium-experimental-timestamp-query-inside-passes")?this.queryType="inside-passes":this.device.features.has("timestamp-query")&&(this.queryType="at-passes"),this.queryType!=="none"&&typeof this.querySet>"u"&&(this.querySet=this.device.createQuerySet({type:"timestamp",count:this.maxDispatchNumber*2}),this.queryResolveBuffer=this.device.createBuffer({size:this.maxDispatchNumber*2*8,usage:GPUBufferUsage.COPY_SRC|GPUBufferUsage.QUERY_RESOLVE})))}captureBegin(){It("info","captureBegin"),this.capturedCommandList.get(this.currentSessionId)||this.capturedCommandList.set(this.currentSessionId,[]),this.capturedPendingKernels.get(this.currentSessionId)||this.capturedPendingKernels.set(this.currentSessionId,[]),this.flush(),this.sessionStatus="capturing"}captureEnd(){It("info","captureEnd"),this.flush(),this.sessionStatus="default"}replay(){It("info","replay"),this.sessionStatus="replaying";let e=this.capturedCommandList.get(this.currentSessionId),r=this.capturedPendingKernels.get(this.currentSessionId),a=e.length;this.pendingKernels=[];for(let s=0;s<a;s++){let o=this.getComputePassEncoder(),p=e[s];this.writeTimestamp(this.pendingDispatchNumber*2),o.setPipeline(p.computePipeline),o.setBindGroup(0,p.bindGroup),o.dispatchWorkgroups(...p.dispatchGroup),this.writeTimestamp(this.pendingDispatchNumber*2+1),this.pendingDispatchNumber++,this.queryType!=="none"&&this.pendingKernels.push(r[s]),(this.pendingDispatchNumber>=this.maxDispatchNumber||this.queryType==="at-passes")&&this.endComputePass(),this.pendingDispatchNumber>=this.maxDispatchNumber&&this.flush()}this.flush(),this.sessionStatus="default"}onCreateSession(){this.gpuDataManager.onCreateSession()}onReleaseSession(e){this.unregisterBuffers(e),this.capturedCommandList.has(e)&&this.capturedCommandList.delete(e),this.capturedPendingKernels.has(e)&&this.capturedPendingKernels.delete(e),this.gpuDataManager.onReleaseSession(e)}onRunStart(e){this.currentSessionId=e,this.setQueryType()}}}),pv={};ys(pv,{init:()=>cv});var Zu,Aw,cv,G1=Ee(()=>{ut(),Ta(),ct(),Y2(),Zu=class hv{constructor(r,a,s,o){this.module=r,this.dataType=a,this.data=s,this.dims=o}getFloat32Array(){if(this.dataType!==1)throw new Error("Invalid data type");let r=ge.size(this.dims);return r===0?new Float32Array:new Float32Array(this.module.HEAP8.buffer,this.data,r)}getBigInt64Array(){if(this.dataType!==7)throw new Error("Invalid data type");let r=ge.size(this.dims);return r===0?new BigInt64Array:new BigInt64Array(this.module.HEAP8.buffer,this.data,r)}getInt32Array(){if(this.dataType!==6)throw new Error("Invalid data type");let r=ge.size(this.dims);return r===0?new Int32Array:new Int32Array(this.module.HEAP8.buffer,this.data,r)}getUint16Array(){if(this.dataType!==10&&this.dataType!==4)throw new Error("Invalid data type");let r=ge.size(this.dims);return r===0?new Uint16Array:new Uint16Array(this.module.HEAP8.buffer,this.data,r)}reshape(r){if(ge.size(r)!==ge.size(this.dims))throw new Error("Invalid new shape");return new hv(this.module,this.dataType,this.data,r)}},Aw=class{constructor(e,r,a){this.module=e,this.backend=r,this.customDataOffset=0,this.customDataSize=0,this.adapterInfo=r.adapterInfo;let s=e.PTR_SIZE,o=a/e.PTR_SIZE,p=s===4?"i32":"i64";this.opKernelContext=Number(e.getValue(s*o++,p));let d=Number(e.getValue(s*o++,p));this.outputCount=Number(e.getValue(s*o++,p)),this.customDataOffset=Number(e.getValue(s*o++,"*")),this.customDataSize=Number(e.getValue(s*o++,p));let g=[];for(let m=0;m<d;m++){let _=Number(e.getValue(s*o++,p)),v=Number(e.getValue(s*o++,"*")),x=Number(e.getValue(s*o++,p)),T=[];for(let C=0;C<x;C++)T.push(Number(e.getValue(s*o++,p)));g.push(new Zu(e,_,v,T))}this.inputs=g}get kernelCustomData(){return this.backend.currentKernelCustomData}get customDataBuffer(){return this.module.HEAPU8.subarray(this.customDataOffset,this.customDataOffset+this.customDataSize)}compute(e,r){var d;let a=((d=r==null?void 0:r.inputs)==null?void 0:d.map(g=>typeof g=="number"?this.inputs[g]:g))??this.inputs,s=(r==null?void 0:r.outputs)??[],o=(g,m,_)=>new Zu(this.module,m,this.output(g,_),_),p=(g,m)=>{let _=Un(g,m);if(!_)throw new Error(`Unsupported data type: ${g}`);let v=_>0?this.backend.gpuDataManager.create(_).id:0;return new Zu(this.module,g,v,m)};return this.backend.run(e,a,s,o,p,this.outputCount)}output(e,r){let a=this.module.stackSave();try{let s=this.module.PTR_SIZE,o=s===4?"i32":"i64",p=this.module.stackAlloc((1+r.length)*s);this.module.setValue(p,r.length,o);for(let d=0;d<r.length;d++)this.module.setValue(p+s*(d+1),r[d],o);return this.module._JsepOutput(this.opKernelContext,e,p)}catch(s){throw new Error(`Failed to generate kernel's output[${e}] with dims [${r}]. If you are running with pre-allocated output, please make sure the output type/dims are correct. Error: ${s}`)}finally{this.module.stackRestore(a)}}},cv=async(e,r,a,s)=>{let o=r.jsepInit;if(!o)throw new Error("Failed to initialize JSEP. The WebAssembly module is not built with JSEP support.");if(e==="webgpu"){let p=(W1(),Go(lv)).WebGpuBackend,d=new p;await d.initialize(a,s),o("webgpu",[d,g=>d.alloc(Number(g)),g=>d.free(g),(g,m,_,v=!1)=>{if(v)It("verbose",()=>`[WebGPU] jsepCopyGpuToGpu: src=${Number(g)}, dst=${Number(m)}, size=${Number(_)}`),d.memcpy(Number(g),Number(m));else{It("verbose",()=>`[WebGPU] jsepCopyCpuToGpu: dataOffset=${Number(g)}, gpuDataId=${Number(m)}, size=${Number(_)}`);let x=r.HEAPU8.subarray(Number(g>>>0),Number(g>>>0)+Number(_));d.upload(Number(m),x)}},async(g,m,_)=>{It("verbose",()=>`[WebGPU] jsepCopyGpuToCpu: gpuDataId=${g}, dataOffset=${m}, size=${_}`),await d.download(Number(g),()=>r.HEAPU8.subarray(Number(m)>>>0,Number(m+_)>>>0))},(g,m,_)=>d.createKernel(g,Number(m),_,r.UTF8ToString(r._JsepGetNodeName(Number(m)))),g=>d.releaseKernel(g),(g,m,_,v)=>{It("verbose",()=>`[WebGPU] jsepRun: sessionHandle=${_}, kernel=${g}, contextDataOffset=${m}`);let x=new Aw(r,d,Number(m));return d.computeKernel(Number(g),x,v)},()=>d.captureBegin(),()=>d.captureEnd(),()=>d.replay()])}else{let p=new x0(a);o("webnn",[p,()=>p.reserveTensorId(),d=>p.releaseTensorId(d),async(d,g,m,_,v)=>p.ensureTensor(d,g,m,_,v),(d,g)=>{p.uploadTensor(d,g)},async(d,g)=>p.downloadTensor(d,g),(d,g)=>p.registerMLContext(d,g),!!a.trace])}}}),Ow,Wp,Gp,tn,Rw,ep,ul,Fp,Hp,tp,jp,Kp,Zp,fv=Ee(()=>{bi(),Z2(),Q2(),ut(),Fn(),Ep(),y0(),Ow=(e,r)=>{Wt()._OrtInit(e,r)!==0&&Pt("Can't initialize onnxruntime.")},Wp=async e=>{Ow(e.wasm.numThreads,il(e.logLevel))},Gp=async(e,r)=>{var s,o;(o=(s=Wt()).asyncInit)==null||o.call(s);let a=e.webgpu.adapter;if(r==="webgpu"){if(typeof navigator>"u"||!navigator.gpu)throw new Error("WebGPU is not supported in current environment");if(a){if(typeof a.limits!="object"||typeof a.features!="object"||typeof a.requestDevice!="function")throw new Error("Invalid GPU adapter set in `env.webgpu.adapter`. It must be a GPUAdapter object.")}else{let p=e.webgpu.powerPreference;if(p!==void 0&&p!=="low-power"&&p!=="high-performance")throw new Error(`Invalid powerPreference setting: "${p}"`);let d=e.webgpu.forceFallbackAdapter;if(d!==void 0&&typeof d!="boolean")throw new Error(`Invalid forceFallbackAdapter setting: "${d}"`);if(a=await navigator.gpu.requestAdapter({powerPreference:p,forceFallbackAdapter:d}),!a)throw new Error('Failed to get GPU adapter. You may need to enable flag "--enable-unsafe-webgpu" if you are using Chrome.')}}if(r==="webnn"&&(typeof navigator>"u"||!navigator.ml))throw new Error("WebNN is not supported in current environment");{let p=(G1(),Go(pv)).init;r==="webgpu"&&await p("webgpu",Wt(),e,a),r==="webnn"&&await p("webnn",Wt(),e)}},tn=new Map,Rw=e=>{let r=Wt(),a=r.stackSave();try{let s=r.PTR_SIZE,o=r.stackAlloc(2*s);r._OrtGetInputOutputCount(e,o,o+s)!==0&&Pt("Can't get session input/output count.");let p=s===4?"i32":"i64";return[Number(r.getValue(o,p)),Number(r.getValue(o+s,p))]}finally{r.stackRestore(a)}},ep=(e,r)=>{let a=Wt(),s=a.stackSave(),o=0;try{let p=a.PTR_SIZE,d=a.stackAlloc(2*p);a._OrtGetInputOutputMetadata(e,r,d,d+p)!==0&&Pt("Can't get session input/output metadata.");let g=Number(a.getValue(d,"*"));o=Number(a.getValue(d+p,"*"));let m=a.HEAP32[o/4];if(m===0)return[g,0];let _=a.HEAPU32[o/4+1],v=[];for(let x=0;x<_;x++){let T=Number(a.getValue(o+8+x*p,"*"));v.push(T!==0?a.UTF8ToString(T):Number(a.getValue(o+8+(x+_)*p,"*")))}return[g,m,v]}finally{a.stackRestore(s),o!==0&&a._OrtFree(o)}},ul=e=>{let r=Wt(),a=r._malloc(e.byteLength);if(a===0)throw new Error(`Can't create a session. failed to allocate a buffer of size ${e.byteLength}.`);return r.HEAPU8.set(e,a),[a,e.byteLength]},Fp=async(e,r)=>{var x,T,C,A;let a,s,o=Wt();Array.isArray(e)?[a,s]=e:e.buffer===o.HEAPU8.buffer?[a,s]=[e.byteOffset,e.byteLength]:[a,s]=ul(e);let p=0,d=0,g=0,m=[],_=[],v=[];try{if([d,m]=await g0(r),(r==null?void 0:r.externalData)&&o.mountExternalData){let B=[];for(let me of r.externalData){let _e=typeof me=="string"?me:me.path;B.push(Cp(typeof me=="string"?me:me.data).then(Re=>{o.mountExternalData(_e,Re)}))}await Promise.all(B)}for(let B of(r==null?void 0:r.executionProviders)??[])if((typeof B=="string"?B:B.name)==="webnn"){if(o.shouldTransferToMLTensor=!1,typeof B!="string"){let me=B,_e=me==null?void 0:me.context,Re=me==null?void 0:me.gpuDevice,Ue=me==null?void 0:me.deviceType,Me=me==null?void 0:me.powerPreference;_e?o.currentContext=_e:Re?o.currentContext=await o.webnnCreateMLContext(Re):o.currentContext=await o.webnnCreateMLContext({deviceType:Ue,powerPreference:Me})}else o.currentContext=await o.webnnCreateMLContext();break}p=await o._OrtCreateSession(a,s,d),(x=o.webgpuOnCreateSession)==null||x.call(o,p),p===0&&Pt("Can't create a session."),(T=o.jsepOnCreateSession)==null||T.call(o),o.currentContext&&(o.webnnRegisterMLContext(p,o.currentContext),o.currentContext=void 0,o.shouldTransferToMLTensor=!0);let[R,H]=Rw(p),U=!!(r!=null&&r.enableGraphCapture),P=[],F=[],G=[],K=[],ee=[];for(let B=0;B<R;B++){let[me,_e,Re]=ep(p,B);me===0&&Pt("Can't get an input name."),_.push(me);let Ue=o.UTF8ToString(me);P.push(Ue),G.push(_e===0?{name:Ue,isTensor:!1}:{name:Ue,isTensor:!0,type:Sa(_e),shape:Re})}for(let B=0;B<H;B++){let[me,_e,Re]=ep(p,B+R);me===0&&Pt("Can't get an output name."),v.push(me);let Ue=o.UTF8ToString(me);F.push(Ue),K.push(_e===0?{name:Ue,isTensor:!1}:{name:Ue,isTensor:!0,type:Sa(_e),shape:Re});{if(U&&(r==null?void 0:r.preferredOutputLocation)===void 0){ee.push("gpu-buffer");continue}let Me=typeof(r==null?void 0:r.preferredOutputLocation)=="string"?r.preferredOutputLocation:((C=r==null?void 0:r.preferredOutputLocation)==null?void 0:C[Ue])??"cpu",pe=o.webnnIsGraphOutput;if(Me==="cpu"&&pe&&pe(p,Ue)){ee.push("ml-tensor-cpu-output");continue}if(Me!=="cpu"&&Me!=="cpu-pinned"&&Me!=="gpu-buffer"&&Me!=="ml-tensor")throw new Error(`Not supported preferred output location: ${Me}.`);if(U&&Me!=="gpu-buffer")throw new Error(`Not supported preferred output location: ${Me}. Only 'gpu-buffer' location is supported when enableGraphCapture is true.`);ee.push(Me)}}let ae=null;return ee.some(B=>B==="gpu-buffer"||B==="ml-tensor"||B==="ml-tensor-cpu-output")&&(g=o._OrtCreateBinding(p),g===0&&Pt("Can't create IO binding."),ae={handle:g,outputPreferredLocations:ee,outputPreferredLocationsEncoded:ee.map(B=>B==="ml-tensor-cpu-output"?"ml-tensor":B).map(B=>np(B))}),tn.set(p,[p,_,v,ae,U,!1]),[p,P,F,G,K]}catch(R){throw _.forEach(H=>o._OrtFree(H)),v.forEach(H=>o._OrtFree(H)),g!==0&&o._OrtReleaseBinding(g)!==0&&Pt("Can't release IO binding."),p!==0&&o._OrtReleaseSession(p)!==0&&Pt("Can't release session."),R}finally{o._free(a),d!==0&&o._OrtReleaseSessionOptions(d)!==0&&Pt("Can't release session options."),m.forEach(R=>o._free(R)),(A=o.unmountExternalData)==null||A.call(o)}},Hp=e=>{var m,_,v;let r=Wt(),a=tn.get(e);if(!a)throw new Error(`cannot release session. invalid session id: ${e}`);let[s,o,p,d,g]=a;d&&(g&&r._OrtClearBoundOutputs(d.handle)!==0&&Pt("Can't clear bound outputs."),r._OrtReleaseBinding(d.handle)!==0&&Pt("Can't release IO binding.")),(m=r.jsepOnReleaseSession)==null||m.call(r,e),(_=r.webnnOnReleaseSession)==null||_.call(r,e),(v=r.webgpuOnReleaseSession)==null||v.call(r,e),o.forEach(x=>r._OrtFree(x)),p.forEach(x=>r._OrtFree(x)),r._OrtReleaseSession(s)!==0&&Pt("Can't release session."),tn.delete(e)},tp=async(e,r,a,s,o,p,d=!1)=>{if(!e){r.push(0);return}let g=Wt(),m=g.PTR_SIZE,_=e[0],v=e[1],x=e[3],T=x,C,A;if(_==="string"&&(x==="gpu-buffer"||x==="ml-tensor"))throw new Error("String tensor is not supported on GPU.");if(d&&x!=="gpu-buffer")throw new Error(`External buffer must be provided for input/output index ${p} when enableGraphCapture is true.`);if(x==="gpu-buffer"){let U=e[2].gpuBuffer;A=Un(Pn(_),v);{let P=g.jsepRegisterBuffer;if(!P)throw new Error('Tensor location "gpu-buffer" is not supported without using WebGPU.');C=P(s,p,U,A)}}else if(x==="ml-tensor"){let U=e[2].mlTensor;A=Un(Pn(_),v);let P=g.webnnRegisterMLTensor;if(!P)throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');C=P(s,U,Pn(_),v)}else{let U=e[2];if(Array.isArray(U)){A=m*U.length,C=g._malloc(A),a.push(C);for(let P=0;P<U.length;P++){if(typeof U[P]!="string")throw new TypeError(`tensor data at index ${P} is not a string`);g.setValue(C+P*m,Ni(U[P],a),"*")}}else{let P=g.webnnIsGraphInput,F=g.webnnIsGraphOutput;if(_!=="string"&&P&&F){let G=g.UTF8ToString(o);if(P(s,G)||F(s,G)){let K=Pn(_);A=Un(K,v),T="ml-tensor";let ee=g.webnnCreateTemporaryTensor,ae=g.webnnUploadTensor;if(!ee||!ae)throw new Error('Tensor location "ml-tensor" is not supported without using WebNN.');let B=await ee(s,K,v);ae(B,new Uint8Array(U.buffer,U.byteOffset,U.byteLength)),C=B}else A=U.byteLength,C=g._malloc(A),a.push(C),g.HEAPU8.set(new Uint8Array(U.buffer,U.byteOffset,A),C)}else A=U.byteLength,C=g._malloc(A),a.push(C),g.HEAPU8.set(new Uint8Array(U.buffer,U.byteOffset,A),C)}}let R=g.stackSave(),H=g.stackAlloc(4*v.length);try{v.forEach((P,F)=>g.setValue(H+F*m,P,m===4?"i32":"i64"));let U=g._OrtCreateTensor(Pn(_),C,A,H,v.length,np(T));U===0&&Pt(`Can't create tensor for input/output. session=${s}, index=${p}.`),r.push(U)}finally{g.stackRestore(R)}},jp=async(e,r,a,s,o,p)=>{var Ue,Me,pe,qe;let d=Wt(),g=d.PTR_SIZE,m=tn.get(e);if(!m)throw new Error(`cannot run inference. invalid session id: ${e}`);let _=m[0],v=m[1],x=m[2],T=m[3],C=m[4],A=m[5],R=r.length,H=s.length,U=0,P=[],F=[],G=[],K=[],ee=[],ae=d.stackSave(),B=d.stackAlloc(R*g),me=d.stackAlloc(R*g),_e=d.stackAlloc(H*g),Re=d.stackAlloc(H*g);try{[U,P]=m0(p),Ln("wasm prepareInputOutputTensor");for(let Ce=0;Ce<R;Ce++)await tp(a[Ce],F,K,e,v[r[Ce]],r[Ce],C);for(let Ce=0;Ce<H;Ce++)await tp(o[Ce],G,K,e,x[s[Ce]],R+s[Ce],C);qn("wasm prepareInputOutputTensor");for(let Ce=0;Ce<R;Ce++)d.setValue(B+Ce*g,F[Ce],"*"),d.setValue(me+Ce*g,v[r[Ce]],"*");for(let Ce=0;Ce<H;Ce++)d.setValue(_e+Ce*g,G[Ce],"*"),d.setValue(Re+Ce*g,x[s[Ce]],"*");if(T&&!A){let{handle:Ce,outputPreferredLocations:nt,outputPreferredLocationsEncoded:Te}=T;if(v.length!==R)throw new Error(`input count from feeds (${R}) is expected to be always equal to model's input count (${v.length}).`);Ln("wasm bindInputsOutputs");for(let Be=0;Be<R;Be++){let We=r[Be];await d._OrtBindInput(Ce,v[We],F[Be])!==0&&Pt(`Can't bind input[${Be}] for session=${e}.`)}for(let Be=0;Be<H;Be++){let We=s[Be];(Ue=o[Be])!=null&&Ue[3]?(ee.push(G[Be]),d._OrtBindOutput(Ce,x[We],G[Be],0)!==0&&Pt(`Can't bind pre-allocated output[${Be}] for session=${e}.`)):d._OrtBindOutput(Ce,x[We],0,Te[We])!==0&&Pt(`Can't bind output[${Be}] to ${nt[Be]} for session=${e}.`)}qn("wasm bindInputsOutputs"),tn.set(e,[_,v,x,T,C,!0])}(Me=d.jsepOnRunStart)==null||Me.call(d,_),(pe=d.webnnOnRunStart)==null||pe.call(d,_);let Ve;T?Ve=await d._OrtRunWithBinding(_,T.handle,H,_e,U):Ve=await d._OrtRun(_,me,B,R,Re,H,_e,U),Ve!==0&&Pt("failed to call OrtRun().");let ze=[],ht=[];Ln("wasm ProcessOutputTensor");for(let Ce=0;Ce<H;Ce++){let nt=Number(d.getValue(_e+Ce*g,"*"));if(nt===G[Ce]||ee.includes(G[Ce])){ze.push(o[Ce]),nt!==G[Ce]&&d._OrtReleaseTensor(nt)!==0&&Pt("Can't release tensor.");continue}let Te=d.stackSave(),Be=d.stackAlloc(4*g),We=!1,Ie,$t=0;try{d._OrtGetTensorData(nt,Be,Be+g,Be+2*g,Be+3*g)!==0&&Pt(`Can't access output tensor data on index ${Ce}.`);let _r=g===4?"i32":"i64",jt=Number(d.getValue(Be,_r));$t=d.getValue(Be+g,"*");let _t=d.getValue(Be+g*2,"*"),er=Number(d.getValue(Be+g*3,_r)),St=[];for(let Ct=0;Ct<er;Ct++)St.push(Number(d.getValue(_t+Ct*g,_r)));d._OrtFree(_t)!==0&&Pt("Can't free memory for tensor dims.");let dr=St.reduce((Ct,He)=>Ct*He,1);Ie=Sa(jt);let hr=T==null?void 0:T.outputPreferredLocations[s[Ce]];if(Ie==="string"){if(hr==="gpu-buffer"||hr==="ml-tensor")throw new Error("String tensor is not supported on GPU.");let Ct=[];for(let He=0;He<dr;He++){let Rt=d.getValue($t+He*g,"*"),sr=d.getValue($t+(He+1)*g,"*"),tr=He===dr-1?void 0:sr-Rt;Ct.push(d.UTF8ToString(Rt,tr))}ze.push([Ie,St,Ct,"cpu"])}else if(hr==="gpu-buffer"&&dr>0){let Ct=d.jsepGetBuffer;if(!Ct)throw new Error('preferredLocation "gpu-buffer" is not supported without using WebGPU.');let He=Ct($t),Rt=Un(jt,dr);if(Rt===void 0||!Ip(Ie))throw new Error(`Unsupported data type: ${Ie}`);We=!0,ze.push([Ie,St,{gpuBuffer:He,download:d.jsepCreateDownloader(He,Rt,Ie),dispose:()=>{d._OrtReleaseTensor(nt)!==0&&Pt("Can't release tensor.")}},"gpu-buffer"])}else if(hr==="ml-tensor"&&dr>0){let Ct=d.webnnEnsureTensor,He=d.webnnIsGraphInputOutputTypeSupported;if(!Ct||!He)throw new Error('preferredLocation "ml-tensor" is not supported without using WebNN.');if(Un(jt,dr)===void 0||!zp(Ie))throw new Error(`Unsupported data type: ${Ie}`);if(!He(e,Ie,!1))throw new Error(`preferredLocation "ml-tensor" for ${Ie} output is not supported by current WebNN Context.`);let Rt=await Ct(e,$t,jt,St,!1);We=!0,ze.push([Ie,St,{mlTensor:Rt,download:d.webnnCreateMLTensorDownloader($t,Ie),dispose:()=>{d.webnnReleaseTensorId($t),d._OrtReleaseTensor(nt)}},"ml-tensor"])}else if(hr==="ml-tensor-cpu-output"&&dr>0){let Ct=d.webnnCreateMLTensorDownloader($t,Ie)(),He=ze.length;We=!0,ht.push((async()=>{let Rt=[He,await Ct];return d.webnnReleaseTensorId($t),d._OrtReleaseTensor(nt),Rt})()),ze.push([Ie,St,[],"cpu"])}else{let Ct=dl(Ie),He=new Ct(dr);new Uint8Array(He.buffer,He.byteOffset,He.byteLength).set(d.HEAPU8.subarray($t,$t+He.byteLength)),ze.push([Ie,St,He,"cpu"])}}finally{d.stackRestore(Te),Ie==="string"&&$t&&d._free($t),We||d._OrtReleaseTensor(nt)}}T&&!C&&(d._OrtClearBoundOutputs(T.handle)!==0&&Pt("Can't clear bound outputs."),tn.set(e,[_,v,x,T,C,!1]));for(let[Ce,nt]of await Promise.all(ht))ze[Ce][2]=nt;return qn("wasm ProcessOutputTensor"),ze}finally{(qe=d.webnnOnRunEnd)==null||qe.call(d,_),d.stackRestore(ae),F.forEach(Ve=>d._OrtReleaseTensor(Ve)),G.forEach(Ve=>d._OrtReleaseTensor(Ve)),K.forEach(Ve=>d._free(Ve)),U!==0&&d._OrtReleaseRunOptions(U),P.forEach(Ve=>d._free(Ve))}},Kp=e=>{let r=Wt(),a=tn.get(e);if(!a)throw new Error("invalid session id");let s=a[0],o=r._OrtEndProfiling(s);o===0&&Pt("Can't get an profile file name."),r._OrtFree(o)},Zp=e=>{let r=[];for(let a of e){let s=a[2];!Array.isArray(s)&&"buffer"in s&&r.push(s.buffer)}return r}}),rn,Yr,cs,Do,No,Qu,rp,Xu,Mn,Dn,Bw,mv,gv,yv,_v,wv,bv,$v,vv=Ee(()=>{bi(),fv(),Fn(),Tp(),rn=()=>!!Ft.wasm.proxy&&typeof document<"u",cs=!1,Do=!1,No=!1,Xu=new Map,Mn=(e,r)=>{let a=Xu.get(e);a?a.push(r):Xu.set(e,[r])},Dn=()=>{if(cs||!Do||No||!Yr)throw new Error("worker not ready")},Bw=e=>{switch(e.data.type){case"init-wasm":cs=!1,e.data.err?(No=!0,rp[1](e.data.err)):(Do=!0,rp[0]()),Qu&&(URL.revokeObjectURL(Qu),Qu=void 0);break;case"init-ep":case"copy-from":case"create":case"release":case"run":case"end-profiling":{let r=Xu.get(e.data.type);e.data.err?r.shift()[1](e.data.err):r.shift()[0](e.data.out);break}}},mv=async()=>{if(!Do){if(cs)throw new Error("multiple calls to 'initWasm()' detected.");if(No)throw new Error("previous call to 'initWasm()' failed.");if(cs=!0,rn())return new Promise((e,r)=>{Yr==null||Yr.terminate(),h0().then(([a,s])=>{try{Yr=s,Yr.onerror=p=>r(p),Yr.onmessage=Bw,rp=[e,r];let o={type:"init-wasm",in:Ft};!o.in.wasm.wasmPaths&&(a||ap)&&(o.in.wasm.wasmPaths={wasm:new URL("/assets/ort-wasm-simd-threaded.jsep-CyqnNavA.wasm",import.meta.url).href}),Yr.postMessage(o),Qu=a}catch(o){r(o)}},r)});try{await kp(Ft.wasm),await Wp(Ft),Do=!0}catch(e){throw No=!0,e}finally{cs=!1}}},gv=async e=>{if(rn())return Dn(),new Promise((r,a)=>{Mn("init-ep",[r,a]);let s={type:"init-ep",in:{epName:e,env:Ft}};Yr.postMessage(s)});await Gp(Ft,e)},yv=async e=>rn()?(Dn(),new Promise((r,a)=>{Mn("copy-from",[r,a]);let s={type:"copy-from",in:{buffer:e}};Yr.postMessage(s,[e.buffer])})):ul(e),_v=async(e,r)=>{if(rn()){if(r!=null&&r.preferredOutputLocation)throw new Error('session option "preferredOutputLocation" is not supported for proxy.');return Dn(),new Promise((a,s)=>{Mn("create",[a,s]);let o={type:"create",in:{model:e,options:{...r}}},p=[];e instanceof Uint8Array&&p.push(e.buffer),Yr.postMessage(o,p)})}else return Fp(e,r)},wv=async e=>{if(rn())return Dn(),new Promise((r,a)=>{Mn("release",[r,a]);let s={type:"release",in:e};Yr.postMessage(s)});Hp(e)},bv=async(e,r,a,s,o,p)=>{if(rn()){if(a.some(d=>d[3]!=="cpu"))throw new Error("input tensor on GPU is not supported for proxy.");if(o.some(d=>d))throw new Error("pre-allocated output tensor is not supported for proxy.");return Dn(),new Promise((d,g)=>{Mn("run",[d,g]);let m=a,_={type:"run",in:{sessionId:e,inputIndices:r,inputs:m,outputIndices:s,options:p}};Yr.postMessage(_,Zp(m))})}else return jp(e,r,a,s,o,p)},$v=async e=>{if(rn())return Dn(),new Promise((r,a)=>{Mn("end-profiling",[r,a]);let s={type:"end-profiling",in:e};Yr.postMessage(s)});Kp(e)}}),ip,Mw,xv,F1=Ee(()=>{bi(),vv(),ut(),Sp(),y0(),ip=(e,r)=>{switch(e.location){case"cpu":return[e.type,e.dims,e.data,"cpu"];case"gpu-buffer":return[e.type,e.dims,{gpuBuffer:e.gpuBuffer},"gpu-buffer"];case"ml-tensor":return[e.type,e.dims,{mlTensor:e.mlTensor},"ml-tensor"];default:throw new Error(`invalid data location: ${e.location} for ${r()}`)}},Mw=e=>{switch(e[3]){case"cpu":return new Qi(e[0],e[2],e[1]);case"gpu-buffer":{let r=e[0];if(!Ip(r))throw new Error(`not supported data type: ${r} for deserializing GPU tensor`);let{gpuBuffer:a,download:s,dispose:o}=e[2];return Qi.fromGpuBuffer(a,{dataType:r,dims:e[1],download:s,dispose:o})}case"ml-tensor":{let r=e[0];if(!zp(r))throw new Error(`not supported data type: ${r} for deserializing MLTensor tensor`);let{mlTensor:a,download:s,dispose:o}=e[2];return Qi.fromMLTensor(a,{dataType:r,dims:e[1],download:s,dispose:o})}default:throw new Error(`invalid data location: ${e[3]}`)}},xv=class{async fetchModelAndCopyToWasmMemory(e){return yv(await Cp(e))}async loadModel(e,r){Xi();let a;typeof e=="string"?a=await this.fetchModelAndCopyToWasmMemory(e):a=e,[this.sessionId,this.inputNames,this.outputNames,this.inputMetadata,this.outputMetadata]=await _v(a,r),Pi()}async dispose(){return wv(this.sessionId)}async run(e,r,a){Xi();let s=[],o=[];Object.entries(e).forEach(x=>{let T=x[0],C=x[1],A=this.inputNames.indexOf(T);if(A===-1)throw new Error(`invalid input '${T}'`);s.push(C),o.push(A)});let p=[],d=[];Object.entries(r).forEach(x=>{let T=x[0],C=x[1],A=this.outputNames.indexOf(T);if(A===-1)throw new Error(`invalid output '${T}'`);p.push(C),d.push(A)});let g=s.map((x,T)=>ip(x,()=>`input "${this.inputNames[o[T]]}"`)),m=p.map((x,T)=>x?ip(x,()=>`output "${this.outputNames[d[T]]}"`):null),_=await bv(this.sessionId,o,g,d,m,a),v={};for(let x=0;x<_.length;x++)v[this.outputNames[d[x]]]=p[x]??Mw(_[x]);return Pi(),v}startProfiling(){}endProfiling(){$v(this.sessionId)}}}),Sv={};ys(Sv,{OnnxruntimeWebAssemblyBackend:()=>wp,initializeFlags:()=>_p,wasmBackend:()=>Tv});var _p,wp,Tv,H1=Ee(()=>{bi(),vv(),F1(),_p=()=>{(typeof Ft.wasm.initTimeout!="number"||Ft.wasm.initTimeout<0)&&(Ft.wasm.initTimeout=0);let e=Ft.wasm.simd;if(typeof e!="boolean"&&e!==void 0&&e!=="fixed"&&e!=="relaxed"&&(console.warn(`Property "env.wasm.simd" is set to unknown value "${e}". Reset it to \`false\` and ignore SIMD feature checking.`),Ft.wasm.simd=!1),typeof Ft.wasm.proxy!="boolean"&&(Ft.wasm.proxy=!1),typeof Ft.wasm.trace!="boolean"&&(Ft.wasm.trace=!1),typeof Ft.wasm.numThreads!="number"||!Number.isInteger(Ft.wasm.numThreads)||Ft.wasm.numThreads<=0)if(typeof self<"u"&&!self.crossOriginIsolated)Ft.wasm.numThreads=1;else{let r=typeof navigator>"u"?A2("node:os").cpus().length:navigator.hardwareConcurrency;Ft.wasm.numThreads=Math.min(4,Math.ceil((r||1)/2))}},wp=class{async init(e){_p(),await mv(),await gv(e)}async createInferenceSessionHandler(e,r){let a=new xv;return await a.loadModel(e,r),a}},Tv=new wp});bi();bi();bi();var j1="1.26.0";{let e=(H1(),Go(Sv)).wasmBackend;hs("webgpu",e,5),hs("webnn",e,5),hs("cpu",e,10),hs("wasm",e,10)}Object.defineProperty(Ft.versions,"web",{value:j1,enumerable:!0});/**
* @license
* Copyright 2021 Google LLC. All Rights Reserved.
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
* =============================================================================
*//**
 * @license
 * Copyright 2020 Google LLC. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * =============================================================================
 *//**
 * @license
 * Copyright 2019 Google LLC. All Rights Reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * =============================================================================
 */Ft.wasm.wasmPaths="/ort/";function K1(e){const r=Vw.utils.encodeWAV(e);return new Blob([r],{type:"audio/wav"})}async function Z1(e){var a;let r;try{r=await Vw.MicVAD.new({baseAssetPath:"/vad/",onnxWASMBasePath:"/ort/",model:"v5",positiveSpeechThreshold:.45,negativeSpeechThreshold:.3,preSpeechPadMs:256,redemptionMs:384,minSpeechMs:250,onSpeechStart:()=>e.onSpeechStart(),onSpeechEnd:s=>{e.onSpeechEnd(K1(s))},onVADMisfire:()=>{var s;return(s=e.onVADMisfire)==null?void 0:s.call(e)}})}catch(s){const o=s instanceof Error?s.name==="NotAllowedError"?"Microphone permission denied.":`VAD init failed: ${s.message}`:"VAD init failed.";throw(a=e.onError)==null||a.call(e,o),s}return r.start(),{stop:async()=>{try{await r.destroy()}catch{}},pause:()=>r.pause(),resume:()=>r.start()}}const Q1={idle:"Ready",listening:"Listening…",recording:"Hearing you",transcribing:"Transcribing…",thinking:"Thinking…",speaking:"Speaking",error:"Error"};function ex({token:e,open:r,onTurn:a,onClose:s,modelOverride:o,reasoningOverride:p}){const[d,g]=ii.useState("idle"),[m,_]=ii.useState(null),[v,x]=ii.useState(!1),T=ii.useRef(null),C=ii.useRef(null),A=ii.useRef(!1),R=ii.useRef(null),H=ii.useRef(a);ii.useEffect(()=>{H.current=a},[a]),ii.useEffect(()=>{A.current=v},[v]);const U=ii.useCallback(()=>{const F=T.current;if(F){try{F.pause(),F.src=""}catch{}T.current=null}const G=R.current;G&&(G.abort(),R.current=null)},[]),P=ii.useCallback(async F=>{g("transcribing");const G=new AbortController;R.current=G;try{const K=new File([F],"voice.wav",{type:"audio/wav"}),{transcript:ee,reply:ae}=await Lm.chatVoice(e,K,{model:o||void 0,reasoning_level:p||void 0});H.current(ee,ae),g("thinking");const B=await Lm.chatTts(e,ae);if(!B){g("listening");return}if(G.signal.aborted)return;g("speaking");const me=URL.createObjectURL(B),_e=new Audio(me);T.current=_e,_e.addEventListener("ended",()=>{URL.revokeObjectURL(me),T.current===_e&&(T.current=null,g("listening"))}),_e.addEventListener("error",()=>{URL.revokeObjectURL(me),T.current===_e&&(T.current=null,g("listening"))}),await _e.play().catch(()=>{URL.revokeObjectURL(me),T.current===_e&&(T.current=null,g("listening"))})}catch(K){if(K instanceof DOMException&&K.name==="AbortError")return;if(K instanceof _2&&K.status===422){H.current(null,null),g("listening");return}const ee=K instanceof Error?K.message:String(K);_(ee),g("error")}finally{R.current===G&&(R.current=null)}},[e,o,p]);return ii.useEffect(()=>{if(!r)return;let F=!0;return g("listening"),_(null),(async()=>{try{const G=await Z1({onSpeechStart:()=>{F&&(T.current&&U(),!A.current&&g("recording"))},onSpeechEnd:K=>{!F||A.current||P(K)},onVADMisfire:()=>{F&&g("listening")},onError:K=>{F&&(_(K),g("error"))}});if(!F){await G.stop();return}C.current=G}catch{}})(),()=>{F=!1,U();const G=C.current;C.current=null,G&&G.stop()}},[r,P,U]),r?Vr.jsxs("div",{className:["fixed inset-0 z-50 flex flex-col items-center justify-between","bg-[var(--color-base)]/95 backdrop-blur","text-[var(--color-fg)]","pt-[max(env(safe-area-inset-top),1.5rem)]","pb-[max(env(safe-area-inset-bottom),1.5rem)]","px-6"].join(" "),role:"dialog","aria-modal":"true","aria-label":"Voice call mode",children:[Vr.jsxs("header",{className:"text-center",children:[Vr.jsx("div",{className:"text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)]",children:"Voice call"}),Vr.jsx("div",{className:"text-sm text-[var(--color-fg-2)] mt-1",children:m??"Speak naturally — I'll listen, reply, and keep going."})]}),Vr.jsx(X1,{state:d}),Vr.jsxs("div",{className:"text-center",children:[Vr.jsx("div",{className:"font-data text-base text-[var(--color-fg)] tabular-nums",children:Q1[d]}),Vr.jsx("div",{className:"text-[11px] text-[var(--color-fg-dim)] mt-1",children:m?"Tap End Call and try again.":d==="speaking"?"Just start talking to interrupt.":"I'll detect when you're done speaking."})]}),Vr.jsxs("div",{className:"flex items-center gap-4",children:[Vr.jsx("button",{type:"button",onClick:()=>x(F=>!F),"aria-label":v?"Unmute microphone":"Mute microphone",className:["w-14 h-14 rounded-full flex items-center justify-center","border-2 transition-colors text-xl",v?"bg-[var(--color-error)] border-[var(--color-error)] text-[var(--color-fg)]":"bg-[var(--color-surface)] border-[var(--color-border-strong)] text-[var(--color-fg)] hover:border-[var(--color-accent)]"].join(" "),children:Vr.jsx("span",{"aria-hidden":!0,children:v?"🚫":"🎤"})}),Vr.jsxs("button",{type:"button",onClick:()=>{U(),s()},"aria-label":"End call",className:["h-14 px-8 rounded-full flex items-center justify-center gap-2","bg-[var(--color-error)] text-[var(--color-fg)] font-semibold","hover:opacity-90 transition-opacity"].join(" "),children:[Vr.jsx("span",{"aria-hidden":!0,children:"📞"}),"End Call"]})]})]}):null}function X1({state:e}){const r="w-44 h-44 rounded-full flex items-center justify-center transition-all duration-300",a="w-24 h-24 rounded-full transition-all duration-300",o={idle:{ring:"border-2 border-[var(--color-border-strong)]",core:"bg-[var(--color-surface-2)]"},listening:{ring:"border-2 border-[var(--color-accent)]/60",core:"bg-[var(--color-accent)]/40",pulse:"animate-pulse"},recording:{ring:"border-4 border-[var(--color-error)]",core:"bg-[var(--color-error)]",pulse:"animate-pulse"},transcribing:{ring:"border-2 border-[var(--color-accent-2)]",core:"bg-[var(--color-accent-2)]/60",pulse:"animate-pulse"},thinking:{ring:"border-2 border-[var(--color-accent)] border-dashed animate-spin",core:"bg-[var(--color-surface-2)]"},speaking:{ring:"border-4 border-[var(--color-accent)] shadow-[0_0_64px_var(--color-accent-2)]",core:"bg-[var(--color-accent)]",pulse:"animate-pulse"},error:{ring:"border-2 border-[var(--color-error)]",core:"bg-[var(--color-error)]/60"}}[e];return Vr.jsx("div",{className:[r,o.ring,o.pulse??""].join(" "),children:Vr.jsx("div",{className:[a,o.core].join(" ")})})}export{ex as VoiceCallMode};
