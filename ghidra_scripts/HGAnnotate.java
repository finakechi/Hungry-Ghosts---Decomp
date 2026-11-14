// Analyst-assigned names only; signatures and dependency names remain unasserted.
// @category HungryGhosts
import ghidra.app.script.GhidraScript;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.listing.*;

public class HGAnnotate extends GhidraScript {
    public void run() throws Exception {
        if (!"a74248c5e2fd2cce9cede6c04d8117ce79e9472d380bbd80eb410370abcf3c61".equals(currentProgram.getExecutableSHA256()))
            throw new IllegalStateException("Wrong ELF");
        name(0x1318f0,"hg_read_poll","Reconstructed, model differential tested. Singleton state at 003eacf0. See analysis/disc_read/functions.json.");
        name(0x1319c0,"hg_read_setup","Request arguments +8..14; clears +0 and +4. Does not clear +18..28.");
        name(0x1319e0,"hg_read_continue","Return 0 busy/next chunk, 1 complete, 2 error. External CDVD operations remain dependencies.");
        name(0x131bf0,"hg_read_start","Sector rounding and first issue; negative length uses whole file size. Return bytes issued or -1.");
        name(0x11d830,"hg_main_loop_candidate","Analyzed startup/main loop lead; not reconstructed. Initializes subsystems, loops on frame semaphore 0095e308.");
        name(0x12fbf0,"hg_lookup_disc_files","Analyzed: searches PACK.DAT and 22 AREA filename table slots. Runtime LBA/size values match disc inventory.");
        name(0x130900,"hg_load_pack_directory_candidate","Analyzed caller: reads directory id << 11 into 003f3b00 and dispatches members. Not reconstructed.");
        label(0x3eacf0,"hg_disc_read_state");
        label(0x3eace4,"hg_disc_read_inhibit");label(0x3eace8,"hg_disc_read_active");
        label(0x3ead20,"hg_pack_file_record");label(0x3f3a30,"hg_area_file_records");
    }
    private void name(long at,String name,String comment) throws Exception {
        Function f=getFunctionAt(toAddr(at));
        if(f==null)throw new IllegalStateException("Missing function "+name);
        f.setName(name,SourceType.USER_DEFINED);
        f.setComment("HG M1 analyst interpretation, 2026-11-14. "+comment);
    }
    private void label(long at,String name) throws Exception { createLabel(toAddr(at),name,true); }
}
