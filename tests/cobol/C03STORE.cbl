      * Conformance: REDEFINES, COMP-3, COMP, signed DISPLAY fields.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. C03STORE.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT          PIC 9(6).
       01  IN-REC.
           05  IN-SIGN       PIC X.
           05  IN-NUM        PIC 9(7).
           05  IN-TXT        PIC X(8).
       01  P3                PIC S9(7)V99 COMP-3.
       01  B4                PIC S9(8) COMP.
       01  SGN-D                PIC S9(7)V99.
       01  GRP.
           05  G-A           PIC X(4).
           05  G-B           PIC X(4).
       01  GRP-R REDEFINES GRP PIC X(8).
       01  NUMX.
           05  NX-1          PIC X.
           05  NX-R          PIC X(6).
       01  NUMV REDEFINES NUMX PIC 9(7).
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM ONE-RECORD WS-COUNT TIMES
           STOP RUN.
       ONE-RECORD.
           ACCEPT IN-REC
           MOVE IN-NUM TO P3
           IF IN-SIGN = '-'
               COMPUTE P3 = P3 * -1
           END-IF
           COMPUTE B4 = P3 / 3
           COMPUTE SGN-D = P3 * 2 - 1000
           MOVE IN-TXT TO GRP-R
           MOVE IN-NUM TO NUMV
           MOVE '7' TO NX-1
           DISPLAY P3 ' ' B4 ' ' SGN-D ' ' G-A ' ' G-B ' ' NUMV.
