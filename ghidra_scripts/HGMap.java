// Apply only to an isolated copy of the verified Japanese database.
// @category HungryGhosts
import ghidra.app.script.GhidraScript;
import ghidra.program.model.mem.MemoryBlock;

public class HGMap extends GhidraScript {
    public void run() throws Exception {
        if (!"a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61".equals(currentProgram.getExecutableSHA256()))
            throw new IllegalStateException("Wrong executable hash");
        // Readelf PT_LOAD[0] file end to memory end, then the union of [1..13].
        add("hg_bss", 0x003ea980L, 0x00a87800L, false);
        add("hg_overlay_arena", 0x00a87800L, 0x00ae5000L, true);
        // Physical EE RAM above the loaded segments, not asserted to be ELF BSS.
        add("hg_remaining_ee_ram", 0x00ae5000L, 0x02000000L, false);
    }
    private void add(String name, long start, long end, boolean execute) throws Exception {
        if (currentProgram.getMemory().getBlock(name) != null)
            throw new IllegalStateException("Map already applied: " + name);
        MemoryBlock b = currentProgram.getMemory().createUninitializedBlock(name,toAddr(start),end-start,false);
        b.setRead(true); b.setWrite(true); b.setExecute(execute);
    }
}
