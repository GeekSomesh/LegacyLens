      * Conformance: signed arithmetic, ROUNDED, numeric-edited output.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. C04EDIT.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT          PIC 9(6).
       01  IN-REC.
           05  IN-SIGN       PIC X.
           05  IN-AMT        PIC 9(6)V99.
           05  IN-RATE       PIC 9V999.
       01  AMT               PIC S9(6)V99.
       01  INTEREST          PIC S9(6)V99.
       01  E1                PIC ZZZ,ZZ9.99-.
       01  E2                PIC $$$,$$9.99.
       01  E3                PIC ZZZZZ9.99CR.
       01  E4                PIC ***,**9.99.
       01  E5                PIC -(6)9.99.
       01  R1                PIC S9(5)V9.
       01  T1                PIC S9(5)V9.
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM ONE-RECORD WS-COUNT TIMES
           STOP RUN.
       ONE-RECORD.
           ACCEPT IN-REC
           MOVE IN-AMT TO AMT
           IF IN-SIGN = '-'
               COMPUTE AMT = AMT * -1
           END-IF
           COMPUTE INTEREST = AMT * IN-RATE / 12
           COMPUTE R1 ROUNDED = AMT / 7
           COMPUTE T1 = AMT / 7
           MOVE AMT TO E1 E3 E5
           MOVE INTEREST TO E2 E4
           DISPLAY E1 '|' E2 '|' E3 '|' E4 '|' E5 '|' R1 '|' T1.
