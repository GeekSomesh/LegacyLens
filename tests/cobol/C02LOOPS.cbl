      * Conformance: OCCURS, PERFORM VARYING, TEST AFTER, inline UNTIL.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. C02LOOPS.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT          PIC 9(6).
       01  IN-REC.
           05  IN-N          PIC 9.
           05  IN-V          PIC 9(2) OCCURS 6.
       01  T.
           05  T-V           PIC 9(3) OCCURS 6.
       01  I                 PIC 9(2).
       01  J                 PIC 9(2).
       01  S                 PIC 9(5).
       01  MX                PIC 9(3).
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM ONE-RECORD WS-COUNT TIMES
           STOP RUN.
       ONE-RECORD.
           ACCEPT IN-REC
           MOVE 0 TO S MX
           PERFORM VARYING I FROM 1 BY 1 UNTIL I > 6
               MOVE IN-V(I) TO T-V(I)
           END-PERFORM
           PERFORM TEST AFTER VARYING J FROM 1 BY 2
                   UNTIL J >= IN-N OR J >= 5
               ADD T-V(J) TO S
           END-PERFORM
           MOVE 1 TO I
           PERFORM UNTIL I > 6
               IF T-V(I) > MX
                   MOVE T-V(I) TO MX
               END-IF
               ADD 1 TO I
           END-PERFORM
           DISPLAY IN-N ' ' S ' ' MX ' ' J.
