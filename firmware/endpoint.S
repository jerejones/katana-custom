# MEMORY-ONLY placement template; placement.py splits real stock spans.
entry:
 addiu v0,zero,126
 beq a2,v0,endpoint
 lui v0,HI_TABLE
 ori v0,v0,LO_TABLE
lookup:
 lbu v1,0(v0)
 beq v1,zero,default
 nop
 bne v1,a2,lookup
 addiu v0,v0,1
found:
 lui v1,HI_TABLE
 ori v1,v1,LO_TABLE
 .word 0x00431023 # subu v0,v0,v1
 sll v0,v0,1
 addu v0,v0,v1
 lhu v0,34(v0)
 lui v1,0x9d02
 or v0,v0,v1
 jr v0
 sll v1,a2,2
 default:
 j 0x9d02dfa0
 sll v1,a2,2
endpoint:
 # Exact source restriction prevents EF forwarding and internal-buffer use.
 addiu v0,zero,1
 bne s2,v0,reject
 lui v0,0xa000
 ori v0,v0,0x50a0
 bne s0,v0,reject
 lhu v0,-31704(gp)
 # gp-0x7bd8 = actual completed EP0 data count, set from BDT in d2ec.
 addiu v1,zero,64
 bne v0,v1,reject
 lbu v0,2(s0)
 addiu v1,zero,13
 bne v0,v1,reject
 lw s3,4(s0)
 sll v0,s3,16
 lui v1,0x4452
 bne v0,v1,reject
 srl v0,s3,16
 addiu v0,v0,-1
 sltiu v0,v0,2
 beq v0,zero,reject
 lbu s4,3(s0)
version_ok:
 sltiu v0,s4,3
 beq v0,zero,reject
 lui s5,0xa001
 addiu s5,s5,-40
 lw s6,-180(s5) # ownership generation before validation
 move s7,zero
 beq s4,zero,reply
 move t0,zero
 # Validate all bytes before publishing or touching display state.
validate:
 addu t1,s0,t0
 lbu t2,8(t1)
 addiu t3,t2,-65
 sltiu t3,t3,26
 bne t3,zero,valid
 addiu t3,t2,-48
 sltiu t3,t3,10
 bne t3,zero,valid
 addiu t3,t2,-32
 sltiu t4,t3,14
 beq t4,zero,reject
 ori t4,zero,0x2c09
 srlv t4,t4,t3
 andi t4,t4,1
 beq t4,zero,reject
 nop
valid:
 sltiu t1,t0,7
 bne t1,zero,validate
 addiu t0,t0,1
 # Atomic admission + grant BEFORE any output. No post-render regrant.
admission:
 srl v0,s3,16
 addiu v1,zero,2
 beq v0,v1,background_request
 nop
 .word 0x41796000 # di t9, saves complete prior Status
 .word 0x000000c0
 addiu s7,zero,1
 lw v0,-180(s5)
 bne v0,s6,admission_unlock
 lbu v0,-32224(gp)
 bne v0,zero,admission_unlock
 lw v0,-32236(gp)
 bne v0,zero,admission_unlock
 lw v0,-32108(gp) # pending deferred feedback work at a00002b4
 bne v0,zero,admission_unlock
 lw v0,-32120(gp) # unconsumed events
 bne v0,zero,admission_unlock
 lw v0,-184(s5) # active electrical writer
 bne v0,zero,admission_unlock
 lbu v0,8(s5)
 bne v0,zero,admission_unlock
 lui t0,0xa001
 jal qualify
 addiu s7,zero,2
 bne v0,zero,admission_unlock
 move s6,zero
 lw v0,-32720(gp)
 bne v0,zero,active
 addiu v0,zero,2
 beq s4,v0,admission_unlock
 addiu s6,zero,1
 sw s6,-32720(gp)
active:
 move s7,zero
 addiu v0,zero,2
 beq s4,v0,clear
 lui v0,0x2020
 lw v0,8(s0)
 b publish
 lw v1,12(s0)
clear:
 ori v0,v0,0x2020
 move v1,v0
publish:
 sw zero,-52(s5) # v1 owner, not background
 sw v0,0(s5)
 sw v1,4(s5)
 lw a2,-180(s5)
 sw a2,-176(s5) # request token
 addiu v0,zero,1
 sw v0,-172(s5) # valid ownership, separately revocable from countdown
 addiu v0,zero,3
 sb v0,8(s5)
 sb s6,12(s5)
 .word 0x40996000 # restore Status, never EI unconditionally
 .word 0x000000c0
 move a0,s5
 jal 0x9d036df8 # direct glyph: no shared wrapper text pointer
 addiu a1,zero,8
 b reply
 nop
admission_unlock:
 .word 0x40996000
 .word 0x000000c0
reply:
 lw t0,-32360(gp)
 sw s3,4(t0)
 sb s7,7(t0)
 srl v0,s3,16
 sb v0,3(t0)
 addiu v1,zero,13
 sb v1,2(t0)
 move t1,s5
 addiu v1,zero,2
 bne v0,v1,reply_text
 nop
 addiu t1,s5,-64
reply_text:
 lw v0,0(t1)
 lw v1,4(t1)
 sw v0,8(t0)
 sw v1,12(t0)
 move a0,s2
 jal 0x9d02b17c
 move a1,t0
reject:
 j 0x9d02dffc
 move s1,zero

# Offline MIPS32 single writer; NO owned deployment placement.
# State at a000ff20: busy/gen/token/valid/count/head/tail/overflows,
# four copied 20-byte {16 segment bytes,length} slots, blank[8].
# Interrupt masking only protects bounded RAM transitions; restore full prior SR.
initialize:
 lui sp,0xa001
 addiu sp,sp,-256
 addiu t0,sp,32
 addiu t1,sp,232
init_loop:
 addiu t0,t0,4
 bne t0,t1,init_loop
 sw zero,-4(t0)
 lui v0,0x2020
 ori v0,v0,0x2020
 sw v0,144(sp)
 j 0x9d005038
 sw v0,148(sp)
# Jump stub for the core unit's service path, placed here because this unit
# has spare bytes and the core unit does not. The core branch reaches it in range.
resume_run:
 j 0x9d00deac
 nop
# Event consumer: execute original captured event load/store, then revoke.
# Only nonzero consumed work revokes; not every idle polling iteration.
event_hook:
 lw v0,-32120(gp)
 sw v0,32(fp)
 beq v0,zero,event_return
 lui t8,0xa001
 .word 0x41796000 # di t9
 .word 0x000000c0 # ehb
 addiu t8,t8,-224
 lw t0,4(t8)
 addiu t0,t0,1
 sw t0,4(t8)
 sw zero,12(t8)
 sw zero,192(t8)
 sw zero,196(t8)
 .word 0x40996000 # mtc0 t9,Status
 .word 0x000000c0
event_return: # event_hook falls through into this return
 j 0x9d00b5e0
 nop
# Common glyph gate: preserve stock frame layout, add per-invocation tag.
# The only custom callers pass private text or private blank. No global
# 'currently custom' flag that a nested stock IRQ could inherit.
render_gate:
 addiu sp,sp,-64
 .word 0x41796000
 .word 0x000000c0
 lui t8,0xa001
 addiu t8,t8,-224
 addiu t0,t8,184
 beq a0,t0,glyph_custom
 addiu t0,t8,112
 beq a0,t0,glyph_custom
 lw t0,4(t8) # harmless when branching: glyph_custom overwrites t0
 addiu t0,t0,1
 sw t0,4(t8)
 sw zero,12(t8)
 sw zero,192(t8)
 sw zero,196(t8)
 sw zero,60(sp)
 b glyph_unlock
 sw zero,56(sp)
glyph_blank:
glyph_custom:
glyph_tag:
 sw a2,56(sp)
 addiu t0,zero,1
 sw t0,60(sp)
glyph_unlock:
 .word 0x40996000
 .word 0x000000c0
 j 0x9d036e00
 sw ra,44(sp) # stock prologue store, deferred into the delay slot
# Replace whole common electrical tail, not just one data call. Command 40,
# data, command 8f and idle GPIO all have one owner.
segment_bridge:
 addiu a0,fp,20
 lbu a1,52(fp)
 sll a1,a1,1
 lw a2,60(fp)
 jal writer
 lw a3,56(fp)
 j 0x9d0373a4
 nop
writer:
 addiu sp,sp,-80
 sw ra,76(sp)
 sw s0,72(sp)
 sw s1,68(sp)
 lui s0,0xa001
 addiu s0,s0,-224
 .word 0x41796000
 .word 0x000000c0
 # Real stock common glyph paths submit <=16 bytes. Defensive refusal of
 # oversized input is explicit, not a memory corruption or unbounded copy.
 sltiu t0,a1,17
 beq t0,zero,writer_unlock_return
 lw t1,12(s0)
 beq a2,zero,stock_submission
 lw t0,0(s0)
 # Custom caller: valid ownership, matching generation, writer idle.
 beq t1,zero,writer_unlock_return
 lw t1,4(s0)
 bne t1,a3,writer_unlock_return
 nop
 bne t0,zero,writer_unlock_return
 nop
take_writer:
 addiu t0,zero,1
 sw t0,0(s0)
 move s1,a1
 jal copy16
 addiu t0,sp,32
writer_unlock_output:
 .word 0x40996000
 .word 0x000000c0
writer_output:
 jal 0x9d036d34
 addiu a0,zero,64
 addiu a0,zero,192
 addiu a1,sp,32
 jal 0x9d036c18
 move a2,s1
 jal 0x9d036d34
 addiu a0,zero,143
 lui v0,0xbf88
 addiu v1,zero,32
 sw v1,0x6338(v0)
 addiu v1,zero,512
 sw v1,0x6338(v0)
 ori v1,zero,0x8000
 sw v1,0x6138(v0)
writer_complete:
 .word 0x41796000
 .word 0x000000c0
 lw t0,16(s0)
 beq t0,zero,writer_idle
 addiu t0,t0,-1
 sw t0,16(s0)
 lw t2,20(s0)
 addiu t1,t2,1
 andi t1,t1,3
 sw t1,20(s0)
 sll t0,t2,4
 sll t1,t2,2
 addu t0,t0,t1
 addu t0,t0,s0
 addiu t0,t0,32
 lw s1,16(t0)
 move a0,t0
 jal copy16
 addiu t0,sp,32
 b writer_unlock_output
 nop
copy16:
 lw t1,0(a0)
 lw t2,4(a0)
 lw t3,8(a0)
 lw t4,12(a0)
 sw t1,0(t0)
 sw t2,4(t0)
 sw t3,8(t0)
 jr ra
 sw t4,12(t0)
writer_idle:
 sw zero,0(s0)
writer_unlock_return:
 .word 0x40996000
 .word 0x000000c0
 lw ra,76(sp)
 lw s0,72(sp)
 lw s1,68(sp)
 jr ra
 addiu sp,sp,80
stock_submission:
 lw t0,0(s0)
 beq t0,zero,take_writer
 lw t0,16(s0)
 # Four FIFO slots. Full: replace newest queued slot, keep earlier three.
 # Surviving frames never reorder; newest stock always retained. Coalescing
 # is observable via overflows, and not advertised as lossless delivery.
 sltiu t1,t0,4
 beq t1,zero,queue_full
 lw t2,24(s0)
 addiu t0,t0,1
 sw t0,16(s0)
 addiu t1,t2,1
 andi t1,t1,3
 b queue_copy
 sw t1,24(s0)
queue_full:
 addiu t2,t2,-1
 andi t2,t2,3
 lw t0,28(s0)
 addiu t0,t0,1
 sw t0,28(s0)
queue_copy:
 sll t0,t2,4
 sll t1,t2,2
 addu t0,t0,t1
 addu t0,t0,s0
 addiu t0,t0,32
 jal copy16
 sw a1,16(t0)
 b writer_unlock_return
 nop
# Called only at the inherited qualified service opportunity.
service:
 .word 0x41796000
 .word 0x000000c0
 lui t0,0xa001
 lbu t1,-32(t0)
 beq t1,zero,background_service
 lbu v0,-32224(gp)
 lw v1,-32236(gp)
 or v0,v0,v1
 bne v0,zero,cancel
 nop
 jal qualify
 nop
 bne v0,zero,cancel
 lw v0,-32720(gp)
 beq v0,zero,cancel
 lbu v0,-28(t0)
 beq v0,zero,countdown
 lbu v0,-32223(gp)
 bne v0,zero,cancel
 nop
countdown:
 addiu t1,t1,-1
 sb t1,-32(t0)
 bne t1,zero,service_unlock
 lbu v0,-28(t0)
 beq v0,zero,expiry_output
 sb zero,-28(t0)
 sw zero,-32720(gp)
expiry_output:
 lw a2,-220(t0)
 .word 0x40996000
 .word 0x000000c0
 addiu a0,t0,-112
 jal 0x9d036df8
 addiu a1,zero,8
 b resume
 nop
cancel:
 sw zero,-32(t0)
 sw zero,-28(t0)
 sw zero,-212(t0)
service_unlock:
 .word 0x40996000
 .word 0x000000c0
resume:
 lw v0,-32112(gp)
 beq v0,zero,resume_run
 nop
 j 0x9d00e3ec
 nop
qualify:
 lbu v0,-30224(t0)
 addiu v0,v0,-1
 lbu v1,-30154(t0)
 andi v1,v1,1
 or v0,v0,v1
 lw v1,-32112(gp)
 or v0,v0,v1
 lw v1,-32152(gp)
 jr ra
 or v0,v0,v1


# RAM: ff98/ff9c background bytes; ffa0 mode 0 absent/1 present/2 clear;
# ffa4 owner kind (1 background); ffa8 idle-blank provenance record, written
# only by idle-provenance.S. All are in existing initialized reserve.
background_request:
 .word 0x41796000
 .word 0x000000c0
 lui t0,0xa001
 addiu v0,zero,2
 beq s4,v0,background_clear
 lui v0,0x2020
 sw zero,-212(t0)
 lw v0,8(s0)
 b background_store
 lw v1,12(s0)
background_clear:
 ori v0,v0,0x2020
 move v1,v0
background_store:
 sw v0,-104(t0)
 sw v1,-100(t0)
 sw s4,-96(t0)
background_changed:
 lw v0,-220(t0)
 addiu v0,v0,1
 jal background_attempt
 sw v0,-220(t0)
 .word 0x40996000
 .word 0x000000c0
 beq a0,zero,reply
 nop
 jal 0x9d036df8
 addiu a1,zero,8
 b reply
 nop
background_service:
 jal background_attempt
 nop
 .word 0x40996000
 .word 0x000000c0
 beq a0,zero,resume
 nop
 jal 0x9d036df8
 addiu a1,zero,8
 b resume
 nop
# Called masked, returns a0=text or zero; a2 immutable generation for blank.
# No I/O while masked; bounded fixed checks/copies; no queue of custom text.
background_attempt:
 move t7,ra
 move a0,zero
 lw t2,-96(t0)
 beq t2,zero,background_return
 lw v0,-212(t0)
 lw v1,-92(t0)
 .word 0x00431024 # and v0,v0,v1
 addiu v1,zero,2
 beq t2,v1,background_clear_check
 nop
background_set_check:
 bne v0,zero,background_return
 nop
background_priority:
 # Reject when any of: standby/inhibitors (qualify), legacy lease, writer
 # busy, stock duration, pending text, deferred work, unconsumed events.
 jal qualify
 lbu t3,-32(t0) # legacy lease, loaded in the call's delay slot
 lw v1,-224(t0) # writer owns GPIO, including queued stock
 or v0,v0,v1
 or v0,v0,t3
 lbu v1,-32224(gp)
 or v0,v0,v1
 lw v1,-32236(gp)
 or v0,v0,v1
 lw v1,-32108(gp)
 or v0,v0,v1
 lw v1,-32120(gp)
 or v0,v0,v1
 bne v0,zero,background_return
 lw v0,-32720(gp)
 bne v0,zero,background_grant
 lw a2,-220(t0)
 # Display is dark. Wake it only for SET, and only when the stock idle
 # timeout darkened it (ffa8 record). An explicit stock display-off never
 # sets that record, so it still wins. Mirror the stock display-on writes:
 # latch word 1 and the idle countdown re-armed from the configured timeout.
 addiu v1,zero,2
 beq t2,v1,background_return # CLEAR never wakes
 lw v1,-88(t0)
 beq v1,zero,background_return # darkened by an explicit stock off
 lbu v1,-30219(t0) # configured idle timeout byte a00089f5
 addiu v0,zero,1
 sw v0,-32720(gp)
 sb v1,-32223(gp)
background_grant:
 sw a2,-216(t0)
 addiu v0,zero,1
 sw v0,-212(t0)
 sw v0,-92(t0)
 addiu v0,zero,2
 beq t2,v0,background_blank
 lw v0,-104(t0)
 lw v1,-100(t0)
 sw v0,-40(t0)
 sw v1,-36(t0)
 b background_return
 addiu a0,t0,-40
background_blank:
 sw zero,-96(t0)
 b background_return
 addiu a0,t0,-112
background_clear_check:
 bne v0,zero,background_priority
 nop
background_forget:
 sw zero,-96(t0)
background_return:
 jr t7
 nop

.align 4
table:
# Generated 36-byte opcode vector (zero sentinel), 35 target halfwords, 2 pad bytes.
.word 100991745
.word 303108103
.word 404165907
.word 488381210
.word 572596254
.word 808199715
.word 1379547447
.word 3231488912
.word 12829377
.word 3150494612
.word 3159931932
.word 3230973252
.word 3254304980
.word 3271607004
.word 3302015932
.word 3374106680
.word 3472673396
.word 3496267764
.word 3505705120
.word 3526414712
.word 3569144800
.word 3587233036
.word 3710964980
.word 3641235556
.word 3728267940
.word 3742686892
.word 57200
end:
