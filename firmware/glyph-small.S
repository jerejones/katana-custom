# Stock byte classification without duplicated alphabet lookup tables.
glyph_compact:
 lw t0,48(fp)
 lbu t1,52(fp)
 beq t1,zero,compact_done
 addiu t2,fp,20
 lui t3,HI_TABLE
compact_loop:
 lbu t4,0(t0)
 ori v0,t4,32
 addiu v0,v0,-97
 sltiu v1,v0,26
 bne v1,zero,compact_fetch
 nop
 addiu v0,t4,-48
 sltiu v1,v0,10
 bne v1,zero,compact_digit
 addiu v0,v0,26
 beq t4,zero,compact_fetch
 addiu v0,zero,55
 addiu v1,t4,-32
 sltiu v0,v1,16
 beq v0,zero,compact_fetch
 addiu v0,zero,41
 addu v1,v1,t3
 lbu v0,LO_TABLE(v1)
compact_unknown:
compact_digit:
compact_fetch:
 sll v0,v0,1
 lui v1,0x9d04
 addu v0,v0,v1
 lhu v0,-2400(v0)
 .word 0xa5420000 # sh v0,0(t2)
 addiu t0,t0,1
 addiu t1,t1,-1
 bne t1,zero,compact_loop
 addiu t2,t2,2
compact_done:
 j 0x9d037344
 nop
.align 4
table:
.word 623454519
.word 690563369
.word 908339497
.word 690566185
