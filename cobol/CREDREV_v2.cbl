      *================================================================*
      * CREDREV  - MONTHLY CREDIT LINE REVIEW (VERSION 2)              *
      * Batch policy program. Reads a count, then one account summary  *
      * per line, and writes one decision line per account:            *
      *   ACCOUNT DECISION REASON UTILISATION                          *
      * Record layout (29 bytes):                                      *
      *   ACCT 9(6) LIMIT 9(7) BILL 9(7) PAID 9(7) DELINQ 9 LATE-6M 9  *
      *================================================================*
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CREDREV.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-COUNT              PIC 9(6).
       01  IN-REC.
           05  IN-ACCT           PIC 9(6).
           05  IN-LIMIT          PIC 9(7).
           05  IN-BILL           PIC 9(7).
           05  IN-PAID           PIC 9(7).
           05  IN-DELINQ         PIC 9.
           05  IN-LATE-6M        PIC 9.
       01  WS-UTIL               PIC 9(5).
       01  WS-MIN-DUE            PIC 9(7).
       01  WS-HIGH-LIMIT         PIC 9(7)  VALUE 400000.
       01  OUT-DECISION          PIC X(8).
       01  OUT-REASON            PIC X(3).
       01  WS-SEVERITY           PIC 9.
           88  SEVERELY-LATE     VALUE 3 THRU 9.
       PROCEDURE DIVISION.
       MAIN-PARA.
           ACCEPT WS-COUNT
           PERFORM REVIEW-ACCOUNT WS-COUNT TIMES
           STOP RUN.

       REVIEW-ACCOUNT.
           ACCEPT IN-REC
           PERFORM COMPUTE-RATIOS
           PERFORM APPLY-POLICY
           DISPLAY IN-ACCT ' ' OUT-DECISION ' ' OUT-REASON ' '
                   WS-UTIL.

       COMPUTE-RATIOS.
      *    Utilisation in percent of the credit limit.
           IF IN-LIMIT = 0
               MOVE 999 TO WS-UTIL
           ELSE
               COMPUTE WS-UTIL = IN-BILL * 100 / IN-LIMIT
           END-IF
      *    Minimum payment due is 10 percent of the statement.
           COMPUTE WS-MIN-DUE = IN-BILL / 10
           MOVE IN-DELINQ TO WS-SEVERITY.

       APPLY-POLICY.
           IF SEVERELY-LATE
               MOVE 'BLOCK' TO OUT-DECISION
               MOVE 'R01' TO OUT-REASON
           ELSE
               IF IN-DELINQ >= 1 AND WS-UTIL > 85
                   MOVE 'REVIEW' TO OUT-DECISION
                   MOVE 'R02' TO OUT-REASON
               ELSE
                   EVALUATE TRUE
                       WHEN IN-LATE-6M >= 3
                           MOVE 'DECREASE' TO OUT-DECISION
                           MOVE 'R03' TO OUT-REASON
                       WHEN IN-PAID < WS-MIN-DUE AND WS-UTIL > 80
                           MOVE 'REVIEW' TO OUT-DECISION
                           MOVE 'R04' TO OUT-REASON
                       WHEN IN-PAID = 0 AND IN-BILL > 100000
                           MOVE 'REVIEW' TO OUT-DECISION
                           MOVE 'R07' TO OUT-REASON
                       WHEN IN-LATE-6M = 0 AND WS-UTIL < 30
                            AND IN-LIMIT < WS-HIGH-LIMIT
                           MOVE 'INCREASE' TO OUT-DECISION
                           MOVE 'R05' TO OUT-REASON
                       WHEN OTHER
                           MOVE 'KEEP' TO OUT-DECISION
                           MOVE 'R06' TO OUT-REASON
                   END-EVALUATE
               END-IF
           END-IF.
