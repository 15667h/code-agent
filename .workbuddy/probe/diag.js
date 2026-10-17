(function(){
  var L=[];
  function r(){
    var pre=document.getElementById("__d")||document.createElement("pre");
    pre.id="__d";
    pre.style.cssText="position:fixed;left:0;top:0;z-index:99999;background:#000;color:#0f0;padding:8px;font:11px/1.5 monospace;white-space:pre-wrap;max-height:100vh;overflow:auto;margin:0";
    pre.textContent=L.join("\n");document.body.appendChild(pre);
  }
  setTimeout(function(){
    var wb=document.querySelector(".workbench");
    var term=document.querySelector(".terminal");
    L.push("sheets: "+[].map.call(document.styleSheets,function(s){return s.href||"(inline)";}).join("\n        "));
    L.push("wb.className="+wb.className);
    L.push("wb inline="+JSON.stringify(wb.getAttribute("style")));
    L.push("html inline="+JSON.stringify(document.documentElement.getAttribute("style")));
    L.push("before rows="+getComputedStyle(wb).gridTemplateRows);
    wb.classList.add("is-term-collapsed");
    void wb.offsetHeight;
    L.push("after className="+wb.className);
    L.push("matches="+wb.matches(".workbench.is-term-collapsed"));
    L.push("after rows="+getComputedStyle(wb).gridTemplateRows);
    L.push("term vis="+getComputedStyle(term).visibility+" h="+Math.round(term.getBoundingClientRect().height));
    var hits=[];
    for(var i=0;i<document.styleSheets.length;i++){
      var sh=document.styleSheets[i],rs;
      try{rs=sh.cssRules;}catch(e){L.push("sheet "+i+" 不可读");continue;}
      for(var j=0;j<rs.length;j++){
        var x=rs[j];
        if(x.selectorText&&x.selectorText.indexOf("is-term-collapsed")>-1)
          hits.push("sheet"+i+"#"+j+"  ["+x.selectorText+"]  => "+x.style.cssText);
      }
    }
    L.push("--- 命中规则 ---");
    L.push(hits.length?hits.join("\n"):"无");
    var st=document.createElement("style");
    st.textContent=".workbench.is-term-collapsed{grid-template-rows:minmax(0,1fr) auto 0 !important}.workbench.is-term-collapsed .terminal{visibility:hidden !important}";
    document.head.appendChild(st);
    void wb.offsetHeight;
    L.push("!important 注入后 rows="+getComputedStyle(wb).gridTemplateRows+" vis="+getComputedStyle(term).visibility);
    r();
  },1500);
})();
