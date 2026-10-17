      * Conformance: GO TO, PERFORM THRU with an EXIT paragraph.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. C01GOTO.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT          PIC 9(6).
       01  IN-REC.
           05  IN-A          PIC 9(3).
           05  IN-B          PIC 9(3).
           05  IN-C          PIC X(2).
       01  OUT-X             PIC 9(5).
       01  OUT-PATH          PIC X(12).
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM ONE-RECORD WS-COUNT TIMES
           STOP RUN.
       ONE-RECORD.
           ACCEPT IN-REC
           MOVE SPACES TO OUT-PATH
           MOVE 0 TO OUT-X
           PERFORM STEP-1 THRU STEP-EXIT
           DISPLAY IN-A ' ' IN-B ' ' OUT-X ' ' OUT-PATH.
       STEP-1.
           IF IN-A > IN-B
               MOVE 'A-BIG' TO OUT-PATH
               GO TO STEP-3
           END-IF
           MOVE 'B-BIG' TO OUT-PATH.
       STEP-2.
           COMPUTE OUT-X = IN-B - IN-A
           IF IN-C = 'XX'
               GO TO STEP-EXIT
           END-IF
           ADD 100 TO OUT-X.
       STEP-3.
           IF OUT-X = 0
               COMPUTE OUT-X = IN-A - IN-B
           END-IF
           ADD 1 TO OUT-X.
       STEP-EXIT.
           EXIT.
