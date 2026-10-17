      *================================================================*
      * FRAUDHLD - CARD AUTHORISATION FRAUD SCREEN                      *
      * Real-time policy: reads a count, then one authorisation request *
      * per line, and writes ACCOUNT ACTION REASON RATIO per request.   *
      * Record layout (38 bytes):                                       *
      *   CARD 9(6) AMOUNT 9(7)V99 AVG-30D 9(7)V99 COUNT-1H 99 MCC 9(4) *
      *   COUNTRY XX HOME XX HOUR 99 CHANNEL X 3DS X                    *
      *================================================================*
       IDENTIFICATION DIVISION.
       PROGRAM-ID. FRAUDHLD.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT              PIC 9(6).
       01  TX-REC.
           05  TX-CARD           PIC 9(6).
           05  TX-AMOUNT         PIC 9(7)V99.
           05  TX-AVG-30D        PIC 9(7)V99.
           05  TX-COUNT-1H       PIC 99.
           05  TX-MCC            PIC 9(4).
               88  GAMBLING      VALUE 7995.
               88  CASH-LIKE     VALUE 6010 6011 6051.
           05  TX-COUNTRY        PIC XX.
           05  TX-HOME           PIC XX.
           05  TX-HOUR           PIC 99.
               88  NIGHT         VALUE 0 THRU 5.
           05  TX-CHANNEL        PIC X.
               88  E-COMMERCE    VALUE 'E'.
           05  TX-3DS            PIC X.
               88  AUTHENTICATED VALUE 'Y'.
      *    High-risk countries: a constant table, built the classic way.
       01  WS-RISK-INIT          PIC X(10) VALUE 'X1X2X3X4X5'.
       01  WS-RISK-TABLE REDEFINES WS-RISK-INIT.
           05  WS-RISK-CTRY      PIC XX OCCURS 5.
       01  WS-I                  PIC 99.
       01  WS-RISK-FLAG          PIC X.
           88  FOREIGN-RISK      VALUE 'Y'.
       01  WS-RATIO              PIC 9(5)V99.
       01  OUT-ACTION            PIC X(9).
       01  OUT-REASON            PIC X(3).
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM SCREEN-TXN WS-COUNT TIMES
           STOP RUN.

       SCREEN-TXN.
           ACCEPT TX-REC
           PERFORM CHECK-COUNTRY
           PERFORM COMPUTE-RATIO
           PERFORM DECIDE
           DISPLAY TX-CARD ' ' OUT-ACTION ' ' OUT-REASON ' ' WS-RATIO.

       CHECK-COUNTRY.
           MOVE 'N' TO WS-RISK-FLAG
           IF TX-COUNTRY NOT = TX-HOME
               PERFORM VARYING WS-I FROM 1 BY 1 UNTIL WS-I > 5
                   IF TX-COUNTRY = WS-RISK-CTRY(WS-I)
                       MOVE 'Y' TO WS-RISK-FLAG
                   END-IF
               END-PERFORM
           END-IF.

       COMPUTE-RATIO.
      *    Amount as a multiple of the 30-day average spend.
           IF TX-AVG-30D = 0
               MOVE 99999.99 TO WS-RATIO
           ELSE
               COMPUTE WS-RATIO = TX-AMOUNT / TX-AVG-30D
           END-IF.

       DECIDE.
           EVALUATE TRUE
               WHEN TX-COUNT-1H > 20
                   MOVE 'DECLINE' TO OUT-ACTION
                   MOVE 'F01' TO OUT-REASON
               WHEN FOREIGN-RISK AND E-COMMERCE
                   MOVE 'DECLINE' TO OUT-ACTION
                   MOVE 'F02' TO OUT-REASON
               WHEN GAMBLING OR CASH-LIKE
                   IF WS-RATIO > 5
                       MOVE 'HOLD' TO OUT-ACTION
                       MOVE 'F03' TO OUT-REASON
                   ELSE
                       MOVE 'CHALLENGE' TO OUT-ACTION
                       MOVE 'F04' TO OUT-REASON
                   END-IF
               WHEN WS-RATIO > 10 AND NIGHT
                   MOVE 'HOLD' TO OUT-ACTION
                   MOVE 'F05' TO OUT-REASON
               WHEN E-COMMERCE AND NOT AUTHENTICATED
                    AND TX-AMOUNT > 500
                   MOVE 'CHALLENGE' TO OUT-ACTION
                   MOVE 'F06' TO OUT-REASON
               WHEN FOREIGN-RISK
                   MOVE 'CHALLENGE' TO OUT-ACTION
                   MOVE 'F07' TO OUT-REASON
               WHEN OTHER
                   MOVE 'APPROVE' TO OUT-ACTION
                   MOVE 'F00' TO OUT-REASON
           END-EVALUATE.
