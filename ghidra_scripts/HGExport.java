// Read-only export after analysis of an isolated, verified program.
// @category HungryGhosts
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.mem.*;
import ghidra.program.model.symbol.*;
import com.google.gson.*;
import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

public class HGExport extends GhidraScript {
    private String addr(Address a) { return String.format("0x%08x", a.getOffset()); }
    private String hex(byte[] b) {
        StringBuilder s = new StringBuilder();
        for (byte x : b) s.append(String.format("%02x", x & 255));
        return s.toString();
    }
    private void save(Path p, Object data) throws Exception {
        try (Writer w = Files.newBufferedWriter(p, StandardOpenOption.CREATE_NEW)) {
            new GsonBuilder().setPrettyPrinting().serializeNulls().create().toJson(data, w);
        }
    }
    public void run() throws Exception {
        String[] args = getScriptArgs();
        HGExportSet transaction = new HGExportSet(Paths.get(args[0]));
        Path out = transaction.staging();
        Map<String,Object> meta = new LinkedHashMap<>();
        meta.put("language", currentProgram.getLanguageID().toString());
        meta.put("compiler_spec", currentProgram.getCompilerSpec().getCompilerSpecID().toString());
        meta.put("sha256", currentProgram.getExecutableSHA256());
        meta.put("ghidra_version", ghidra.framework.Application.getApplicationVersion());
        List<Object> blocks = new ArrayList<>();
        for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
            Map<String,Object> row = new LinkedHashMap<>();
            row.put("name", b.getName()); row.put("start", addr(b.getStart()));
            row.put("address_space", b.getStart().getAddressSpace().getName());
            row.put("end_exclusive", String.format("0x%08x", b.getEnd().getOffset()+1));
            row.put("initialized", b.isInitialized()); row.put("execute", b.isExecute());
            row.put("read", b.isRead()); row.put("write", b.isWrite()); row.put("mapped", b.isMapped());
            blocks.add(row);
        }
        meta.put("blocks", blocks);
        List<Object> inventory = new ArrayList<>();
        for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
            if (f.isExternal()) continue;
            Map<String,Object> row = new LinkedHashMap<>();
            row.put("entry", addr(f.getEntryPoint())); row.put("name", f.getName());
            row.put("body_bytes", f.getBody().getNumAddresses());
            List<Object> ranges = new ArrayList<>();
            for (AddressRange range : f.getBody().getAddressRanges())
                ranges.add(Map.of("start", addr(range.getMinAddress()), "end_exclusive",
                                  String.format("0x%08x", range.getMaxAddress().getOffset()+1)));
            row.put("body_ranges", ranges);
            List<String> calls = new ArrayList<>();
            for (Function target : f.getCalledFunctions(monitor)) calls.add(addr(target.getEntryPoint()));
            Collections.sort(calls); row.put("calls", calls);
            row.put("status", "auto-discovered-unreviewed"); inventory.add(row);
        }
        meta.put("function_count", inventory.size());
        save(out.resolve("map.json"), meta);
        save(out.resolve("functions.json"), inventory);
        // String references are investigation leads, not function names.
        try (PrintWriter w = new PrintWriter(Files.newBufferedWriter(out.resolve("strings.tsv"), StandardOpenOption.CREATE_NEW))) {
            for (Data d : currentProgram.getListing().getDefinedData(true)) {
                if (!d.hasStringValue()) continue;
                String value = String.valueOf(d.getValue()).replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r");
                ReferenceIterator refs = currentProgram.getReferenceManager().getReferencesTo(d.getAddress());
                List<String> rr = new ArrayList<>();
                for (Reference r : refs) rr.add(addr(r.getFromAddress()));
                w.println(addr(d.getAddress()) + "\t" + value + "\t" + String.join(",",rr));
            }
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        for (int i = 1; i < args.length; i++) {
            Address address = toAddr(Long.decode(args[i]));
            Function f = getFunctionContaining(address);
            if (f == null) throw new IOException("Missing requested function " + args[i]);
            Map<String,Object> record = new LinkedHashMap<>();
            record.put("entry", addr(f.getEntryPoint())); record.put("name", f.getName());
            List<Object> ranges = new ArrayList<>();
            for (AddressRange range : f.getBody().getAddressRanges())
                ranges.add(Map.of("start",addr(range.getMinAddress()),"end_exclusive",
                                  String.format("0x%08x",range.getMaxAddress().getOffset()+1)));
            record.put("body_ranges",ranges);
            List<Object> instructions = new ArrayList<>();
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Instruction ins : currentProgram.getListing().getInstructions(f.getBody(),true)) {
                byte[] bytes = ins.getBytes(); digest.update(bytes);
                instructions.add(Map.of("address",addr(ins.getAddress()),"bytes",hex(bytes),"text",ins.toString()));
            }
            record.put("instructions",instructions); record.put("listing_sha256",hex(digest.digest()));
            List<Object> refs = new ArrayList<>();
            for (Reference r : currentProgram.getReferenceManager().getReferencesTo(f.getEntryPoint()))
                refs.add(Map.of("from",addr(r.getFromAddress()),"to",addr(r.getToAddress()),"type",r.getReferenceType().toString()));
            record.put("references_to",refs);
            DecompileResults result = decompiler.decompileFunction(f,60,monitor);
            record.put("decompiler_complete",result.decompileCompleted());
            record.put("decompiler_error",result.getErrorMessage());
            record.put("decompiler",result.decompileCompleted()?result.getDecompiledFunction().getC():null);
            save(out.resolve(String.format("%08x.json", f.getEntryPoint().getOffset())), record);
        }
        decompiler.dispose();
        monitor.checkCancelled();
        transaction.finish();
        println("HG export: " + inventory.size() + " functions, language " + meta.get("language"));
    }
}
