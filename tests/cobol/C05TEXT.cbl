      * Conformance: 88 lists, SET TRUE, EVALUATE THRU, reference
      * modification, INITIALIZE, numeric to text moves.
       IDENTIFICATION DIVISION.
       PROGRAM-ID. C05TEXT.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT          PIC 9(6).
       01  IN-REC.
           05  IN-CODE       PIC X(4).
           05  IN-N          PIC 9(3).
       01  W-STATUS          PIC X.
           88  ST-OK         VALUE 'A' 'B' 'C'.
           88  ST-BAD        VALUE 'X'.
       01  W-GROUP.
           05  WG-A          PIC X(3) VALUE 'ABC'.
           05  WG-N          PIC 9(3) VALUE 123.
           05  WG-P          PIC S9(3) COMP-3 VALUE -5.
       01  W-TXT             PIC X(10).
       01  W-CAT             PIC X(6).
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM ONE-RECORD WS-COUNT TIMES
           STOP RUN.
       ONE-RECORD.
           ACCEPT IN-REC
           MOVE IN-CODE(1:1) TO W-STATUS
           MOVE '??????' TO W-CAT
           EVALUATE TRUE
               WHEN ST-OK
                   MOVE 'OK' TO W-CAT
               WHEN ST-BAD
                   MOVE 'BAD' TO W-CAT
               WHEN IN-CODE(2:2) = 'ZZ'
                   MOVE 'ZZ' TO W-CAT
               WHEN OTHER
                   CONTINUE
           END-EVALUATE
           EVALUATE IN-N
               WHEN 0 THRU 99
                   MOVE 'LOW' TO W-TXT
               WHEN 100 THRU 499
                   MOVE 'MID' TO W-TXT
               WHEN OTHER
                   MOVE IN-N TO W-TXT
           END-EVALUATE
           IF IN-N > 900
               INITIALIZE W-GROUP
           END-IF
           IF IN-CODE = 'SETX'
               SET ST-BAD TO TRUE
           END-IF
           MOVE IN-CODE(3:2) TO W-TXT(5:2)
           DISPLAY W-STATUS ' ' W-CAT ' ' W-TXT ' ' WG-A ' ' WG-N ' '
                   WG-P.
