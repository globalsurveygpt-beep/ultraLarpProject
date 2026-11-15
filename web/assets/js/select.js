/* Custom select enhancement. The native <select> keeps the value, the label and form behaviour;
   this draws the closed control and the list in the workspace style. */
(function(){
 'use strict';
 const valueProp=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value');
 let open=null;
 function close(restore=false){if(!open)return;const {button,list}=open;list.remove();button.setAttribute('aria-expanded','false');button.removeAttribute('aria-activedescendant');open=null;if(restore)button.focus();}
 function enhance(select,index){
  const wrap=document.createElement('div');wrap.className='select-field';select.parentNode.insertBefore(wrap,select);wrap.appendChild(select);
  const button=document.createElement('button'),label=document.querySelector('label[for="'+select.id+'"]'),listId='lucy-select-'+index;
  button.type='button';button.className='select-toggle';button.setAttribute('role','combobox');button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-expanded','false');button.setAttribute('aria-controls',listId);
  if(label){if(!label.id)label.id=listId+'-label';button.setAttribute('aria-labelledby',label.id);label.addEventListener('click',e=>{e.preventDefault();button.focus();});}
  button.innerHTML='<span></span><svg viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 4.5 6 8l3.5-3.5"/></svg>';
  select.tabIndex=-1;select.setAttribute('aria-hidden','true');wrap.appendChild(button);
  const options=()=>Array.from(select.options);
  const refresh=()=>{const o=select.options[select.selectedIndex];button.firstChild.textContent=o?o.textContent:'';button.disabled=select.disabled;};
  // Code that assigns select.value directly must also update the visible control.
  Object.defineProperty(select,'value',{configurable:true,get(){return valueProp.get.call(this);},set(v){valueProp.set.call(this,v);refresh();}});
  select.addEventListener('change',refresh);
  function choose(i){const o=options()[i];if(!o||o.disabled)return;const changed=select.selectedIndex!==i;select.selectedIndex=i;refresh();close(true);if(changed){select.dispatchEvent(new Event('input',{bubbles:true}));select.dispatchEvent(new Event('change',{bubbles:true}));}}
  function setActive(i){if(!open)return;const items=Array.from(open.list.children);if(!items.length)return;i=Math.max(0,Math.min(items.length-1,i));open.active=i;items.forEach((li,n)=>li.classList.toggle('active',n===i));button.setAttribute('aria-activedescendant',items[i].id);items[i].scrollIntoView({block:'nearest'});}
  function show(){
   close();const list=document.createElement('ul');list.className='select-list';list.id=listId;list.setAttribute('role','listbox');if(label)list.setAttribute('aria-labelledby',label.id);
   options().forEach((o,i)=>{const li=document.createElement('li');li.id=listId+'-option-'+i;li.setAttribute('role','option');li.setAttribute('aria-selected',String(i===select.selectedIndex));if(o.disabled)li.setAttribute('aria-disabled','true');li.textContent=o.textContent;li.onmousemove=()=>{if(open&&open.active!==i)setActive(i);};li.onclick=()=>choose(i);list.appendChild(li);});
   document.body.appendChild(list);open={select,button,list,active:select.selectedIndex};button.setAttribute('aria-expanded','true');
   const r=button.getBoundingClientRect();list.style.minWidth=r.width+'px';const h=list.offsetHeight;list.style.left=Math.max(8,Math.min(r.left,window.innerWidth-list.offsetWidth-8))+'px';list.style.top=(r.bottom+h+8<window.innerHeight?r.bottom+4:Math.max(8,r.top-h-4))+'px';
   setActive(Math.max(0,select.selectedIndex));
  }
  let typed='',typedTimer;
  button.onclick=()=>{if(open&&open.select===select)close(true);else show();};
  button.onkeydown=e=>{
   const isOpen=open&&open.select===select,n=options().length;
   if(e.key==='Escape'){if(isOpen){e.preventDefault();close(true);}return;}
   if(e.key==='Tab'){if(isOpen)close();return;}
   if(e.key==='Enter'||e.key===' '){e.preventDefault();if(isOpen)choose(open.active);else show();return;}
   const step={ArrowDown:1,ArrowUp:-1}[e.key];
   if(step!==undefined||e.key==='Home'||e.key==='End'){e.preventDefault();if(!isOpen){show();return;}const dir=step||(e.key==='Home'?1:-1);let i=e.key==='Home'?0:e.key==='End'?n-1:open.active+step;while(i>=0&&i<n&&options()[i].disabled)i+=dir;if(i>=0&&i<n)setActive(i);return;}
   if(e.key.length===1&&!e.ctrlKey&&!e.metaKey&&!e.altKey){typed+=e.key.toLowerCase();clearTimeout(typedTimer);typedTimer=setTimeout(()=>typed='',600);const i=options().findIndex(o=>!o.disabled&&o.textContent.trim().toLowerCase().startsWith(typed));if(i>=0){if(isOpen)setActive(i);else choose(i);}}
  };
  refresh();
 }
 document.querySelectorAll('select').forEach(enhance);
 document.addEventListener('pointerdown',e=>{if(open&&!open.list.contains(e.target)&&!open.button.contains(e.target))close();});
 window.addEventListener('resize',()=>close());
 document.addEventListener('scroll',e=>{if(open&&!open.list.contains(e.target))close();},true);
})();
