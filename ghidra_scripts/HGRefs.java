// Read-only address references for bounded investigations.
// @category HungryGhosts
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;
public class HGRefs extends GhidraScript {
    public void run() throws Exception {
        String[] args=getScriptArgs(); List<Object> rows=new ArrayList<>();
        for(int i=1;i<args.length;i++) {
            Address a=toAddr(Long.decode(args[i]));
            for(Reference r:currentProgram.getReferenceManager().getReferencesTo(a)) {
                if(r.getFromAddress().getOffset()>=0x02000000L)continue;
                Function f=getFunctionContaining(r.getFromAddress());
                rows.add(Map.of("target",a.toString(),"from",r.getFromAddress().toString(),"type",r.getReferenceType().toString(),
                    "function",f==null?"unknown":f.getEntryPoint().toString()));
            }
        }
        Files.writeString(Paths.get(args[0]),new GsonBuilder().setPrettyPrinting().create().toJson(rows));
    }
}
